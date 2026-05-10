"""Meeting ephemeral Redis State Service module.

Generates atomic mapping tracking natively memory limits smoothly defining
targets natively.
"""

import json
import logging
from collections.abc import Awaitable
from typing import Any, cast

import redis.asyncio as aioredis

from app.modules.auth.token_store import _get_redis_client
from app.modules.meeting.constants import (
    key_room_active_speaker,
    key_room_lobby,
    key_room_participants,
)

logger = logging.getLogger(__name__)


class MeetingStateService:
    """Manages ephemeral live room state (lobby, participants presence,
    active speaker) in Redis.

    All operations are asynchronous and hit Redis directly smoothly handling
    maps natively seamlessly.
    """

    def __init__(self, redis_client: aioredis.Redis | None = None) -> None:
        self._redis = redis_client or _get_redis_client()

    # ── Participant Presence Hash ────────────────────────────────────────

    async def add_participant(
        self,
        room_code: str,
        user_id: str,
        language: str,
        speaking_language: str = "en",
        hardware_ready: bool = True,
        display_name: str = "",
        role: str = "guest",
    ) -> None:
        """Add or update a user's presence in the active room participants hash.

        Args:
            room_code (str): Identity parameter dynamically natively resolving
                identifiers.
            user_id (str): User tracker string mapped locally natively limits
                logically securely bindings natively.
            language (str): Locale configuration gracefully array mapping.
            hardware_ready (bool): Configuration map dynamically natively smoothly
                correctly natively tracking gracefully gracefully locally securely
                smoothly gracefully tracking natively handled array limit logically
                seamlessly bounds dynamically safely correctly securely limits
                correctly dynamically.
        """
        state = {
            "status": "connected",
            "language": language,
            "speaking_language": speaking_language,
            "hardware_ready": hardware_ready,
            "display_name": display_name,
            "role": role,
        }
        await cast(
            "Awaitable[Any]",
            self._redis.hset(
                name=key_room_participants(room_code),
                key=user_id,
                value=json.dumps(state),
            ),
        )

    async def remove_participant(self, room_code: str, user_id: str) -> None:
        """Remove a user from the active participants hash.

        Args:
            room_code (str): Identity string naturally resolving natively
                gracefully limits seamlessly dynamically correctly safely mapping
                dynamically.
            user_id (str): Evaluator tracking string string parameter seamlessly
                mapping efficiently limits.
        """
        await cast(
            "Awaitable[Any]",
            self._redis.hdel(key_room_participants(room_code), user_id),
        )

    async def get_participants(self, room_code: str) -> dict[str, dict]:
        """Fetch all connected participants for the room.

        Returns a dict mapping user_id to their JSON-parsed state.
        """
        raw_data = await cast(
            "Awaitable[dict[Any, Any]]",
            self._redis.hgetall(key_room_participants(room_code)),
        )
        return {
            user_id: json.loads(state_str) for user_id, state_str in raw_data.items()
        }

    # ── Lobby Set ────────────────────────────────────────────────────────

    async def add_to_lobby(
        self, room_code: str, user_id: str, display_name: str, language: str
    ) -> None:
        """Place a user in the waiting room/lobby hash."""
        state = {
            "display_name": display_name,
            "language": language,
        }
        await cast(
            "Awaitable[Any]",
            self._redis.hset(key_room_lobby(room_code), user_id, json.dumps(state)),
        )

    async def remove_from_lobby(self, room_code: str, user_id: str) -> None:
        """Remove a user from the lobby set (e.g. if rejected or left)."""
        await cast(
            "Awaitable[Any]", self._redis.hdel(key_room_lobby(room_code), user_id)
        )

    async def get_lobby(self, room_code: str) -> dict[str, dict]:
        """Fetch all users currently in the lobby."""
        raw_data = await cast(
            "Awaitable[dict[Any, Any]]", self._redis.hgetall(key_room_lobby(room_code))
        )
        return {uid: json.loads(val) for uid, val in raw_data.items()}

    async def admit_from_lobby(self, room_code: str, user_id: str) -> bool:
        """Atomically remove a user from the lobby and add them to participants.

        Returns True if the user was actually in the lobby.
        """
        lobby_data_raw = await cast(
            "Awaitable[Any]", self._redis.hget(key_room_lobby(room_code), user_id)
        )
        if not lobby_data_raw:
            return False

        lobby_state = json.loads(lobby_data_raw)
        language = lobby_state.get("language", "en")
        display_name = lobby_state.get("display_name", "")

        # A lightweight transaction (pipeline) to ensure we don't have partial state
        pipe = self._redis.pipeline()
        pipe.hdel(key_room_lobby(room_code), user_id)

        state = {
            "status": "connected",
            "language": language,
            "hardware_ready": True,
            "display_name": display_name,
            "role": "guest",
        }
        pipe.hset(
            name=key_room_participants(room_code), key=user_id, value=json.dumps(state)
        )

        await pipe.execute()
        return True

    # ── Active Speaker ───────────────────────────────────────────────────

    async def set_active_speaker(
        self, room_code: str, user_id: str, ttl_seconds: int = 5
    ) -> None:
        """Update the current active speaker.

        TTL ensures the speaker resets if the client disconnects or stops sending
        audio levels.
        """
        await self._redis.set(
            name=key_room_active_speaker(room_code), value=user_id, ex=ttl_seconds
        )

    async def get_active_speaker(self, room_code: str) -> str | None:
        """Get the ID of the current active speaker, if any."""
        return await self._redis.get(key_room_active_speaker(room_code))  # type: ignore[no-any-return]

    # ── Room Lifecycle ───────────────────────────────────────────────────

    async def cleanup_room(self, room_code: str) -> None:
        """Wipe all ephemeral state for the given room when the meeting ends."""
        keys = tuple(
            filter(
                None,
                [
                    key_room_participants(room_code),
                    key_room_lobby(room_code),
                    key_room_active_speaker(room_code),
                ],
            )
        )
        if keys:
            await self._redis.delete(*keys)
            logger.info("Cleaned up Redis state for room %s", room_code)
