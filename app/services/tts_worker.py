"""TTS (Text-to-Speech) Kafka consumer worker.

Consumes translated text from ``text.translated``, calls the configured
TTS provider (OpenAI or Voice.ai), and publishes synthesized audio
to ``audio.synthesized``.

The active provider is controlled by ``settings.ACTIVE_TTS_PROVIDER``.
"""

import base64
import logging
import time
from typing import Any

from app.core.config import settings
from app.external_services.openai_tts.service import get_openai_tts_service
from app.external_services.voiceai.service import get_voiceai_tts_service
from app.kafka.consumer import BaseConsumer
from app.kafka.schemas import BaseEvent
from app.kafka.topics import AUDIO_SYNTHESIZED, TEXT_TRANSLATED
from app.schemas.pipeline import (
    AudioEncoding,
    SynthesizedAudioEvent,
    SynthesizedAudioPayload,
    TranslationEvent,
)

logger = logging.getLogger(__name__)


class TTSWorker(BaseConsumer):
    """Kafka consumer that synthesizes translated text into audio.

    Subscribes to ``text.translated`` and publishes
    ``SynthesizedAudioEvent`` messages to ``audio.synthesized``.

    Supports two providers (switchable via ``ACTIVE_TTS_PROVIDER``):
        - ``"openai"`` — OpenAI TTS (tts-1)
        - ``"voiceai"`` — Voice.ai TTS (voiceai-tts-multilingual-v1-latest)

    Attributes:
        topic: The Kafka topic for incoming translated text events.
        group_id: Consumer group identifier for TTS generation.
        event_schema: Pydantic schema used to validate incoming translation events.
    """

    topic = TEXT_TRANSLATED
    group_id = "tts-worker-group"
    event_schema = TranslationEvent
    max_message_age_ms = 120_000  # skip translations from dead sessions

    async def handle(self, event: BaseEvent[Any]) -> None:
        """Process a translation: synthesize audio → publish.

        Args:
            event (BaseEvent[Any]): The deserialized wrapper containing the
                TranslationPayload.
        """
        tl_event = TranslationEvent.model_validate(event.model_dump())
        payload = tl_event.payload

        pipeline_start = time.monotonic()

        text = payload.translated_text.strip()
        if not text:
            logger.warning(
                "Empty translated text for seq=%d, skipping TTS",
                payload.sequence_number,
            )
            return

        # 1. Call the configured TTS provider
        encoding = settings.PIPELINE_AUDIO_ENCODING
        audio_result = await self._synthesize(
            text=text,
            language=payload.target_language,
            encoding=encoding,
        )

        audio_bytes = audio_result["audio_bytes"]
        sample_rate = audio_result["sample_rate"]

        # 2. Base64 encode for Kafka transport
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

        # 3. Build and publish synthesized audio event
        synth_payload = SynthesizedAudioPayload(
            room_id=payload.room_id,
            user_id=payload.user_id,
            sequence_number=payload.sequence_number,
            audio_data=audio_b64,
            target_language=payload.target_language,
            sample_rate=sample_rate,
            encoding=AudioEncoding(encoding),
        )
        synth_event = SynthesizedAudioEvent(payload=synth_payload)

        await self._producer.send(AUDIO_SYNTHESIZED, synth_event, key=payload.room_id)

        # 4. Log pipeline latency
        elapsed_ms = (time.monotonic() - pipeline_start) * 1000
        logger.info(
            "TTS: seq=%d room=%s lang=%s provider=%s audio_size=%d latency=%.1fms",
            payload.sequence_number,
            payload.room_id,
            payload.target_language,
            settings.ACTIVE_TTS_PROVIDER,
            len(audio_bytes),
            elapsed_ms,
        )

    async def _synthesize(self, *, text: str, language: str, encoding: str) -> dict:
        """Dispatch to the active TTS provider.

        Args:
            text (str): The translated native text to synthesize.
            language (str): The language code of the text.
            encoding (str): The desired output audio format encoding.

        Returns:
            dict: A dictionary containing 'audio_bytes' and the 'sample_rate'
                metadata.
        """
        provider = settings.ACTIVE_TTS_PROVIDER.lower()

        if provider == "voiceai":
            return await get_voiceai_tts_service().synthesize(
                text, language=language, encoding=encoding
            )

        # Default: OpenAI
        return await get_openai_tts_service().synthesize(text, encoding=encoding)
