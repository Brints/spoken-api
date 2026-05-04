"""STT (Speech-to-Text) Kafka consumer worker.

Consumes raw audio chunks from ``audio.raw``, calls the Deepgram STT API,
and publishes transcription results to ``text.original``.
"""

import base64
import logging
import time
from typing import Any

from app.external_services.deepgram.service import get_deepgram_stt_service
from app.kafka.consumer import BaseConsumer
from app.kafka.schemas import BaseEvent
from app.kafka.topics import AUDIO_RAW, TEXT_ORIGINAL
from app.schemas.pipeline import (
    AudioChunkEvent,
    TranscriptionEvent,
    TranscriptionPayload,
)

logger = logging.getLogger(__name__)


class STTWorker(BaseConsumer):
    """Kafka consumer that transcribes audio chunks via Deepgram.

    Subscribes to ``audio.raw`` and publishes ``TranscriptionEvent``
    messages to ``text.original``.
    """

    topic = AUDIO_RAW
    group_id = "stt-worker-group"
    event_schema = AudioChunkEvent

    async def handle(self, event: BaseEvent[Any]) -> None:
        """Process a single audio chunk: decode → STT → publish transcript."""
        chunk_event = AudioChunkEvent.model_validate(event.model_dump())
        payload = chunk_event.payload

        pipeline_start = time.monotonic()

        # 1. Decode base64 audio
        audio_bytes = base64.b64decode(payload.audio_data)

        if not audio_bytes:
            logger.warning(
                "Empty audio chunk seq=%d from user=%s, skipping",
                payload.sequence_number,
                payload.user_id,
            )
            return

        # 2. Call Deepgram STT (or Mock it if no API Key provided)
        from app.core.config import settings

        if not settings.DEEPGRAM_API_KEY:
            logger.info("DEEPGRAM_API_KEY not set. Mocking STT response for testing.")
            result: dict[str, Any] = {
                "text": (
                    "Hello, this is a simulated transcription for testing purposes."
                ),
                "detected_language": payload.source_language,
                "confidence": 1.0,
            }
        else:
            stt_service = get_deepgram_stt_service()
            result = await stt_service.transcribe(
                audio_bytes,
                language=payload.source_language,
                sample_rate=payload.sample_rate,
                encoding=payload.encoding.value,
            )

        text = result.get("text", "").strip()
        if not text:
            logger.debug(
                "No speech detected in chunk seq=%d from user=%s",
                payload.sequence_number,
                payload.user_id,
            )
            return

        # 3. Build and publish transcription event
        transcription_payload = TranscriptionPayload(
            room_id=payload.room_id,
            user_id=payload.user_id,
            sequence_number=payload.sequence_number,
            text=text,
            source_language=result.get("detected_language", payload.source_language),
            is_final=True,
            confidence=result.get("confidence", 0.0),
        )
        transcription_event = TranscriptionEvent(payload=transcription_payload)

        await self._producer.send(
            TEXT_ORIGINAL, transcription_event, key=payload.room_id
        )

        # Broadcast active speaker event over WebSocket
        try:
            import asyncio

            from app.services.connection_manager import get_connection_manager

            manager = get_connection_manager()
            task = asyncio.create_task(
                manager.broadcast_to_room(
                    payload.room_id,
                    {
                        "type": "active_speaker_changed",
                        "user_id": payload.user_id,
                    },
                )
            )
            # Fix RUF006: Store a reference to the task to avoid garbage collection
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except Exception as e:
            logger.error("Failed to broadcast active speaker: %s", e)

        # 4. Log pipeline latency
        elapsed_ms = (time.monotonic() - pipeline_start) * 1000
        logger.info(
            "STT: seq=%d room=%s user=%s text='%s' confidence=%.2f latency=%.1fms",
            payload.sequence_number,
            payload.room_id,
            payload.user_id,
            text[:50],
            result.get("confidence", 0.0),
            elapsed_ms,
        )
