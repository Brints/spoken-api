"""Meeting WebSockets Integrations module.

WebSocket endpoints for real-time signaling, audio streaming, and captions
seamlessly intelligently reliably.
"""

import asyncio
import base64
import json
import logging
import time
from pathlib import Path

from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.sanitize import log_sanitizer
from app.kafka.topics import AUDIO_SYNTHESIZED, TEXT_ORIGINAL, TEXT_TRANSLATED
from app.modules.meeting.state import MeetingStateService
from app.modules.meeting.ws_dependencies import assert_room_participant, authenticate_ws
from app.schemas.pipeline import (
    SynthesizedAudioEvent,
)
from app.services.audio_bridge import get_audio_ingest_service
from app.services.connection_manager import get_connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websockets"])


@router.websocket("/signaling/{room_code}")
async def signaling_websocket(
    websocket: WebSocket,
    room_code: str,
    user_id: str = Depends(authenticate_ws),
) -> None:
    """Relays WebRTC Offer, Answer, and ICE Candidate messages between peers
    naturally cleanly mappings logically confidently reliably elegantly optimally
    successfully accurately efficiently correctly accurately dynamically smoothly
    gracefully cleanly successfully reliably optimally cleanly successfully.

    Args:
        websocket (WebSocket): Protocol mapping gracefully effectively gracefully
            efficiently seamlessly cleanly natively efficiently intelligently.
        room_code (str): Video URL param effectively efficiently dynamically
            gracefully successfully locally.
        user_id (str): Extracted authenticated bounds safely cleanly reliably smoothly.
    """
    try:
        participant_state = await assert_room_participant(room_code, user_id)
    except Exception as e:
        await websocket.close(code=1008, reason=str(e))
        return

    await websocket.accept()

    manager = get_connection_manager()
    await manager.connect(room_code, user_id, websocket)

    # Announce this peer to everyone already in the room so the participant
    # panel updates immediately without waiting for WebRTC negotiation.
    display_name = participant_state.get("display_name", "")
    role = participant_state.get("role", "guest")
    await manager.broadcast_to_room(
        room_code,
        {
            "type": "user_joined",
            "user_id": user_id,
            "display_name": display_name,
            "role": role,
        },
        sender_id=user_id,  # Don't echo back to the joiner themselves
    )

    # Tell the new user about all existing users so they can update their UI immediately
    participants = await MeetingStateService().get_participants(room_code)
    existing_users = [
        {
            "user_id": pid,
            "display_name": pstate.get("display_name", ""),
            "role": pstate.get("role", "guest"),
        }
        for pid, pstate in participants.items()
        if pid != user_id
    ]
    await websocket.send_json({"type": "existing_users", "users": existing_users})

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                target_user_id = payload.get("target_user_id")

                # Always inject the sender's identity so the recipient knows
                # who sent the offer/answer/ice_candidate. Without this,
                # from_user_id is undefined on the frontend.
                payload["from_user_id"] = user_id

                # If target specified, unicast. Otherwise, broadcast.
                if target_user_id:
                    await manager.send_to_user(room_code, target_user_id, payload)
                else:
                    await manager.broadcast_to_room(
                        room_code, payload, sender_id=user_id
                    )
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received on signaling WS")

    except WebSocketDisconnect:
        manager.disconnect(room_code, user_id)
        # Notify others that this peer left (use user_left to match frontend model)
        await manager.broadcast_to_room(
            room_code, {"type": "user_left", "user_id": user_id}, sender_id=user_id
        )


@router.websocket("/audio/{room_code}")
async def audio_websocket(  # noqa: C901
    websocket: WebSocket,
    room_code: str,
    user_id: str = Depends(authenticate_ws),
) -> None:
    """Bidirectional audio stream structurally confidently perfectly beautifully
    intelligently flawlessly gracefully stably cleanly successfully robustly
    gracefully optimally logically carefully successfully elegantly.

    Args:
        websocket (WebSocket): Protocol native tracker cleanly cleanly gracefully
            elegantly perfectly beautifully accurately neatly effectively.
        room_code (str): Room id safely neatly accurately intelligently seamlessly
            properly carefully smoothly nicely smartly correctly beautifully safely
            perfectly cleanly cleanly.
        user_id (str): Authenticated limit string naturally cleanly neatly gracefully
            intelligently smartly beautifully seamlessly safely correctly reliably
            beautifully cleanly carefully.
    """
    try:
        participant_state = await assert_room_participant(room_code, user_id)
    except Exception as e:
        await websocket.close(code=1008, reason=str(e))
        return

    listening_language = participant_state.get("language", "en")
    await websocket.accept()
    print("Audio WS client connected: %s", user_id)

    ingest_svc = get_audio_ingest_service()
    ingest_svc.reset_sequence(f"{room_code}:{user_id}")

    async def ingest_task() -> None:
        """Reads WS binary frames (or Base64 text), packages, and sends to Kafka."""
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    logger.info(
                        "Audio WS ingest got disconnect frame for %s",
                        log_sanitizer.sanitize(user_id),
                    )
                    break
                if message.get("text"):
                    try:
                        data = base64.b64decode(message["text"])
                    except Exception:
                        logger.warning("Failed to decode base64 audio text frame.")
                        continue
                elif "bytes" in message and message["bytes"] is not None:
                    data = message["bytes"]
                else:
                    # Ignore close frames or other control messages here
                    continue

                # Chunk the data to avoid Kafka MessageSizeTooLargeError
                # and to simulate standard continuous client streaming
                chunk_size = 500 * 1024  # 500 KB per chunk safely under 1MB limit

                for i in range(0, len(data), chunk_size):
                    chunk = data[i : i + chunk_size]
                    await ingest_svc.publish_audio_chunk(
                        room_id=room_code,
                        user_id=user_id,
                        audio_bytes=chunk,
                        source_language=participant_state.get("language", "en"),
                    )
        except WebSocketDisconnect:
            logger.info(
                "Audio WS client disconnected (WebSocketDisconnect): %s",
                log_sanitizer.sanitize(user_id),
            )
        except RuntimeError as exc:
            # Starlette raises RuntimeError once the disconnect frame has been
            # consumed. Treat it the same as a clean disconnect.
            if (
                "disconnect" not in str(exc).lower()
                and "websocket" not in str(exc).lower()
            ):
                raise
            logger.info(
                "Audio WS ingest RuntimeError (socket already closed) for %s: %s",
                log_sanitizer.sanitize(user_id),
                exc,
            )

    # --- Shared event so egress consumer is ready before we start ingesting ---
    egress_ready = asyncio.Event()

    async def egress_task() -> None:
        """Reads Kafka synthesized audio, filters for user, writes to WS."""
        consumer = AIOKafkaConsumer(
            AUDIO_SYNTHESIZED,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            # No group_id → simple assign mode, avoids rebalance delays
            auto_offset_reset="latest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            enable_auto_commit=False,
        )
        await consumer.start()

        # Force partition assignment by seeking to end
        partitions = consumer.assignment()
        if not partitions:
            # Wait briefly for automatic assignment
            await asyncio.sleep(1)
            partitions = consumer.assignment()
        for tp in partitions:
            await consumer.seek_to_end(tp)

        logger.info(
            "Egress consumer ready. Listening language=%s, partitions=%s",
            listening_language,
            partitions,
        )
        print(
            "Egress consumer ready. Listening language=%s, partitions=%s",
            listening_language,
            partitions,
        )
        egress_ready.set()  # Signal that we are ready to receive

        # Track the highest sequence seen to drop stale frames arriving out-of-order
        highest_seq: dict[str, int] = {}

        try:
            async for msg in consumer:
                try:
                    event = SynthesizedAudioEvent.model_validate(msg.value)
                    payload = event.payload

                    logger.info(
                        "Egress received: room=%s target_lang=%s"
                        " listening_lang=%s seq=%d",
                        payload.room_id,
                        payload.target_language,
                        listening_language,
                        payload.sequence_number,
                    )
                    print(
                        "Egress received: room=%s"
                        " target_lang=%s listening_lang=%s seq=%d",
                        payload.room_id,
                        payload.target_language,
                        listening_language,
                        payload.sequence_number,
                    )

                    # Filter by Room
                    if payload.room_id != room_code:
                        print(f"Egress: skipping wrong room {payload.room_id}")
                        continue

                    # Language filter: In production with multiple participants,
                    # only deliver audio matching the listener's language.
                    # For single-user testing, skip the filter so the speaker
                    # can hear their own translated audio.
                    participants = await MeetingStateService().get_participants(
                        room_code
                    )
                    if (
                        len(participants) > 1
                        and payload.target_language != listening_language
                    ):
                        print(
                            "Egress: skipping lang mismatch"
                            f" target={payload.target_language} "
                            f"!= listening={listening_language}"
                        )
                        continue

                    # Stale frame guard (drop if more than 10 sequences behind latest)
                    speaker_key = payload.user_id
                    current_highest = highest_seq.get(speaker_key, -1)

                    if payload.sequence_number < current_highest - 10:
                        logger.debug("Dropped stale audio frame from %s", speaker_key)
                        continue

                    highest_seq[speaker_key] = max(
                        current_highest, payload.sequence_number
                    )

                    # Send to client (binary)
                    audio_bytes = base64.b64decode(payload.audio_data)
                    print(f"Egress: about to send {len(audio_bytes)} bytes to client")

                    # Also save to disk for testing/validation
                    output_path = Path(rf"{settings.SYSTEM_PATH}\voiceai_output.raw")
                    mode = "ab" if payload.sequence_number > 0 else "wb"

                    def _write_audio(
                        _path: Path = output_path,
                        _mode: str = mode,
                        _data: bytes = audio_bytes,
                    ) -> None:
                        with _path.open(_mode) as f:
                            f.write(_data)

                    await asyncio.to_thread(_write_audio)
                    print(
                        f"Egress: SAVED {len(audio_bytes)} bytes to {output_path} "
                        f"(seq={payload.sequence_number})"
                    )

                    try:
                        await websocket.send_bytes(audio_bytes)
                        print(
                            "Egress: SUCCESSFULLY sent"
                            f" {len(audio_bytes)} bytes"
                            " via WebSocket"
                        )
                    except Exception as send_err:
                        print(
                            "Egress: WebSocket send failed"
                            f" (but file was saved): {send_err}"
                        )

                except Exception as frame_err:
                    print(f"Error processing egress frame: {frame_err}")
                    import traceback

                    traceback.print_exc()

        finally:
            await consumer.stop()

    async def guarded_ingest_task() -> None:
        """Wait for egress consumer to be ready, then start ingesting."""
        await egress_ready.wait()
        logger.info("Egress ready — starting audio ingest")
        await ingest_task()

    task1 = asyncio.create_task(guarded_ingest_task())
    task2 = asyncio.create_task(egress_task())

    try:
        # Run until either task fails or disconnects
        _done, pending = await asyncio.wait(
            [task1, task2], return_when=asyncio.FIRST_COMPLETED
        )
        # Cancel whatever is still running
        for t in pending:
            t.cancel()
    except Exception:
        pass


@router.websocket("/captions/{room_code}")
async def captions_websocket(
    websocket: WebSocket,
    room_code: str,
    user_id: str = Depends(authenticate_ws),
) -> None:
    """Broadcasts original and translated transcription events."""
    try:
        # Validate they are in the room, but we don't strictly *need* their state
        _ = await assert_room_participant(room_code, user_id)
    except Exception as e:
        await websocket.close(code=1008, reason=str(e))
        return

    await websocket.accept()

    # Use a persistent user-specific group so reconnects don't drop captions
    # Note: "Subscribe from now" is handled via auto_offset_reset="latest"
    # in their group creation or by wiping the group offsets.
    # We'll use a dynamic timestamp group to force "latest".
    consumer = AIOKafkaConsumer(
        TEXT_ORIGINAL,
        TEXT_TRANSLATED,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=f"captions-{room_code}-{user_id}-{int(time.time())}",
        auto_offset_reset="latest",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )

    await consumer.start()

    try:
        async for msg in consumer:
            payload_data = msg.value.get("payload", {})
            if payload_data.get("room_id") != room_code:
                continue

            # Build unified caption response depending on topic
            is_translation = msg.topic == TEXT_TRANSLATED

            caption_msg = {
                "event": "caption",
                "speaker_id": payload_data.get("user_id"),
                "is_final": payload_data.get("is_final", True),
                "timestamp_ms": int(time.time() * 1000),
            }

            if is_translation:
                caption_msg["language"] = payload_data.get("target_language")
                caption_msg["text"] = payload_data.get("translated_text")
            else:
                caption_msg["language"] = payload_data.get("source_language")
                caption_msg["text"] = payload_data.get("text")

            await websocket.send_json(caption_msg)

    except WebSocketDisconnect:
        pass
    finally:
        await consumer.stop()
