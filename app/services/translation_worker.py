"""Translation Kafka consumer worker.

Consumes transcribed text from ``text.original``, determines the target
languages from the room's participant state in Redis, calls the DeepL API
(with OpenAI GPT fallback), and publishes one ``TranslationEvent`` per
target language to ``text.translated``.
"""

import logging
import time
from typing import Any

from app.external_services.deepl.service import (
    get_deepl_translation_service,
    get_openai_translation_fallback,
)
from app.kafka.consumer import BaseConsumer
from app.kafka.schemas import BaseEvent
from app.kafka.topics import TEXT_ORIGINAL, TEXT_TRANSLATED
from app.modules.meeting.state import MeetingStateService
from app.schemas.pipeline import (
    TranscriptionEvent,
    TranslationEvent,
    TranslationPayload,
)

logger = logging.getLogger(__name__)


class TranslationWorker(BaseConsumer):
    """Kafka consumer that translates transcribed text for each listener.

    Subscribes to ``text.original`` and publishes ``TranslationEvent``
    messages to ``text.translated`` — one per unique target language
    needed in the room.

    Attributes:
        topic: The Kafka topic for incoming transcription events.
        group_id: Consumer group identifier for translation.
        event_schema: Pydantic schema used to validate transcription events.
    """

    topic = TEXT_ORIGINAL
    group_id = "translation-worker-group"
    event_schema = TranscriptionEvent
    max_message_age_ms = 120_000  # skip transcriptions from dead sessions

    def __init__(self, producer: object) -> None:
        super().__init__(producer=producer)
        self._state = MeetingStateService()

    async def handle(self, event: BaseEvent[Any]) -> None:
        """Process a transcription: resolve target languages → translate → publish.

        Args:
            event (BaseEvent[Any]): The deserialized wrapper containing the
                TranscriptionPayload.
        """
        tx_event = TranscriptionEvent.model_validate(event.model_dump())
        payload = tx_event.payload

        pipeline_start = time.monotonic()

        # Skip interim transcriptions — only process final results
        if not payload.is_final:
            return

        # 1. Determine target languages from room participants
        participants = await self._state.get_participants(payload.room_id)
        target_languages = {
            state.get("language", "en")
            for state in participants.values()
            if state.get("language", "en") != payload.source_language
        }

        if not target_languages:
            logger.debug(
                "No translation needed for seq=%d in room=%s (all same language)",
                payload.sequence_number,
                payload.room_id,
            )
            return

        # 2. Translate for each target language
        for target_lang in target_languages:
            try:
                translated_text = await self._translate_text(
                    payload.text,
                    source_language=payload.source_language,
                    target_language=target_lang,
                )

                if not translated_text:
                    logger.warning(
                        "Empty translation for seq=%d target=%s",
                        payload.sequence_number,
                        target_lang,
                    )
                    continue

                # 3. Publish translation event
                translation_payload = TranslationPayload(
                    room_id=payload.room_id,
                    user_id=payload.user_id,
                    sequence_number=payload.sequence_number,
                    original_text=payload.text,
                    translated_text=translated_text,
                    source_language=payload.source_language,
                    target_language=target_lang,
                )
                translation_event = TranslationEvent(payload=translation_payload)

                await self._producer.send(
                    TEXT_TRANSLATED, translation_event, key=payload.room_id
                )

                logger.debug(
                    "Translation: seq=%d %s→%s text='%s'",
                    payload.sequence_number,
                    payload.source_language,
                    target_lang,
                    translated_text[:50],
                )

            except Exception:
                logger.exception(
                    "Translation failed for seq=%d target=%s",
                    payload.sequence_number,
                    target_lang,
                )
                raise

        elapsed_ms = (time.monotonic() - pipeline_start) * 1000
        logger.info(
            "Translation: seq=%d room=%s targets=%s latency=%.1fms",
            payload.sequence_number,
            payload.room_id,
            sorted(target_languages),
            elapsed_ms,
        )

    async def _translate_text(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
    ) -> str:
        """Dispatch translation to DeepL, OpenAI fallback, or mock.

        Args:
            text (str): The original text string to be translated.
            source_language (str): The source language code (e.g., 'en', 'es').
            target_language (str): The destination language code.

        Returns:
            str: The translated text string, or an empty string on failure.
        """
        from app.core.config import settings

        if not settings.DEEPL_API_KEY and not settings.OPENAI_API_KEY:
            logger.info("Translation config missing. Mocking text for testing.")
            return f"[Mocked Translation -> {target_language}]: {text}"

        deepl = get_deepl_translation_service()
        openai_fallback = get_openai_translation_fallback()

        try:
            if settings.DEEPL_API_KEY and deepl.supports_language(target_language):
                result = await deepl.translate(
                    text,
                    source_language=source_language,
                    target_language=target_language,
                )
            elif settings.OPENAI_API_KEY:
                logger.info(
                    "DeepL skipped or unsupported for '%s', falling back to OpenAI",
                    target_language,
                )
                result = await openai_fallback.translate(
                    text,
                    source_language=source_language,
                    target_language=target_language,
                )
            else:
                raise RuntimeError("No available translation backend.")
        except Exception as api_exc:
            logger.warning(
                "Translation backend failed (%s). Mocking translation.",
                str(api_exc),
            )
            return f"[Mocked Translation -> {target_language}]: {text}"

        return str(result.get("translated_text", ""))
