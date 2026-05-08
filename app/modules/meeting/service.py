"""Meeting core business service module.

Coordinates meeting lifecycle boundaries, room configurations, and Redis state
aggregations seamlessly.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.core.exceptions import (
    BadRequestException,
    ForbiddenException,
    InternalServerException,
    NotFoundException,
)
from app.modules.auth.models import User
from app.modules.meeting.constants import (
    MAX_ROOM_CODE_RETRIES,
    ROOM_CODE_BYTE_LENGTH,
    ParticipantRole,
    RoomStatus,
)
from app.modules.meeting.models import MeetingInvitation, Participant, Room
from app.modules.meeting.repository import MeetingRepository
from app.modules.meeting.schemas import RoomConfigUpdate, RoomSettings
from app.modules.meeting.state import MeetingStateService
from app.services.connection_manager import get_connection_manager
from app.services.email_producer import get_email_producer_service

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


# ── Internal Helpers ──────────────────────────────────────────────────
def _generate_room_code() -> str:
    """Returns a 12-char URL-safe string."""
    return secrets.token_urlsafe(ROOM_CODE_BYTE_LENGTH)


def _build_join_url(room_code: str) -> str:
    return f"{settings.FRONTEND_BASE_URL}/meet/{room_code}"


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    rem_minutes = minutes % 60
    return f"{hours} hours, {rem_minutes} minutes"


def _create_guest_token(session_id: str, display_name: str) -> str:
    payload = {"sub": session_id, "name": display_name, "type": "guest"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)  # type: ignore[no-any-return]


class MeetingService:
    """Orchestrates room lifecycles, permissions, and integrates DB with Redis
    state securely natively."""

    def __init__(self, repo: MeetingRepository, state: MeetingStateService) -> None:
        self.repo = repo
        self.state = state

    # ── Core Operations ───────────────────────────────────────────────────

    def create_room(
        self,
        host: User,
        name: str,
        room_settings: RoomSettings | None,
        scheduled_at: datetime | None,
    ) -> Room:
        """Create a new room and add the creator as the host participant.

        Args:
            host (User): Profile bound identifier natively securely handling data.
            name (str): The configuration defining room array parameter locally
                securely bindings.
            room_settings (RoomSettings | None): Extra values payload natively.
            scheduled_at (datetime | None): Native mapped datetime value efficiently
                natively tracking states.

        Returns:
            Room: A DB entity naturally dynamically extracted from schema natively.
        """

        # 1. Generate unique room code with retries
        room_code = None
        for _ in range(MAX_ROOM_CODE_RETRIES):
            candidate = _generate_room_code()
            if not self.repo.room_code_exists(candidate):
                room_code = candidate
                break

        if not room_code:
            logger.error(
                "Failed to generate unique room code after %d attempts",
                MAX_ROOM_CODE_RETRIES,
            )
            raise InternalServerException(message="Failed to generate room code.")

        if not scheduled_at:
            scheduled_at = utc_now()

        # 2. Build room object
        new_room = Room(
            room_code=room_code,
            host_id=host.id,
            name=name,
            status=RoomStatus.PENDING.value,
            scheduled_at=scheduled_at,
        )
        if room_settings:
            new_room.settings = room_settings.model_dump(exclude_unset=True)

        # 3. Persist room
        new_room = self.repo.create_room(new_room)

        # 4. Create host participant
        host_ptc = Participant(
            room_id=new_room.id,
            user_id=host.id,
            display_name=host.full_name or host.email,
            role=ParticipantRole.HOST.value,
        )
        self.repo.create_participant(host_ptc)

        # We do NOT initialize Redis state here yet because the room is "pending",
        # and no one has physically joined the realtime session.

        # Inject dynamic URL for response
        new_room.join_url = _build_join_url(room_code)  # type: ignore[attr-defined]
        return new_room

    async def get_room_details(self, room_code: str) -> Room:
        """Fetch DB room details and merge with live Redis participant count.

        Args:
            room_code (str): Dynamic mapping variable seamlessly tracked native
                URL bindings.

        Returns:
            Room: Synchronously injected entity tracking dynamic counts elegantly
                natively.
        """
        room = self.repo.get_room_by_code(room_code)
        if not room:
            raise NotFoundException(message="Room not found.")

        # Base properties
        room.join_url = _build_join_url(room_code)  # type: ignore[attr-defined]

        # If it's active, we fetch live count from Redis. Otherwise,
        # DB participant count.
        if room.status == RoomStatus.ACTIVE.value:
            pts = await self.state.get_participants(room_code)
            room.participant_count = len(pts)  # type: ignore[attr-defined]
        else:
            room.participant_count = self.repo.count_all_participants(room.id)  # type: ignore[attr-defined]
            if room.status == RoomStatus.ENDED.value:
                room.total_participants = room.participant_count  # type: ignore[attr-defined]
                if room.ended_at:
                    delta = room.ended_at - room.created_at
                    room.duration = _format_duration(int(delta.total_seconds()))  # type: ignore[attr-defined]

        return room

    async def get_live_state(self, host: User, room_code: str) -> dict:
        """Fetch active participant and waiting lobby details. Host only."""
        room = self.repo.get_room_by_code(room_code)
        if not room:
            raise NotFoundException(message="Room not found.")

        if room.host_id != host.id:
            raise ForbiddenException(
                message="Only the host can view live room state payload."
            )

        active = await self.state.get_participants(room_code)
        lobby = await self.state.get_lobby(room_code)

        return {"active": active, "lobby": lobby}

    # ── join_room helpers ────────────────────────────────────────────────

    def _validate_room_for_join(self, room: Room, user: User | None) -> bool:
        """Validate room state and auto-activate if the host is joining.

        Returns True when the joining user is the host.
        Raises on ended/pending/scheduled rooms as appropriate.
        """
        if room.status == RoomStatus.ENDED.value:
            raise NotFoundException(message="This meeting has already ended.")

        is_host = bool(user and (room.host_id == user.id))

        if room.status == RoomStatus.PENDING.value:
            if is_host:
                room.status = RoomStatus.ACTIVE.value
                self.repo.update_room(room)
            else:
                if room.scheduled_at:
                    # Normalize to naive UTC for comparison (SQLite strips tzinfo)
                    sched = (
                        room.scheduled_at.replace(tzinfo=None)
                        if room.scheduled_at.tzinfo
                        else room.scheduled_at
                    )
                    now = utc_now().replace(tzinfo=None)
                    if sched > now:
                        raise BadRequestException(
                            code="MEETING_NOT_STARTED",
                            message="This meeting is scheduled for a future time.",
                        )
                raise BadRequestException(
                    code="MEETING_NOT_STARTED",
                    message="The host has not started this meeting yet.",
                )

        return is_host

    def _resolve_identity(
        self,
        room: Room,
        user: User | None,
        guest_session_id: str | None,
        guest_name: str | None,
    ) -> tuple[Participant | None, str, str, str | None]:
        """Look up existing participant, build tracking id & display name.

        Returns (participant | None, tracking_id, display_name, guest_token | None).
        """
        user_uuid = user.id if user else None
        guest_uuid = uuid.UUID(guest_session_id) if guest_session_id else None

        ptc = None
        if user_uuid or guest_uuid:
            ptc = self.repo.get_participant(
                room.id, user_id=user_uuid, guest_session_id=guest_uuid
            )

        is_rejoining = ptc is not None

        tracking_id = (
            str(user.id) if user else (guest_session_id if guest_session_id else None)
        )

        if not user and not is_rejoining and not guest_name:
            raise BadRequestException(
                code="MISSING_NAME", message="display_name is required for guests."
            )

        display_name = (
            user.full_name or user.email
            if user
            else (ptc.display_name if ptc else guest_name)
        )

        new_guest_token = None
        if not tracking_id:
            tracking_id = str(uuid.uuid4())
            new_guest_token = _create_guest_token(tracking_id, guest_name or "Guest")

        return ptc, tracking_id, str(display_name), new_guest_token

    async def _check_lobby_required(
        self,
        room: Room,
        room_code: str,
        *,
        is_host: bool,
        is_rejoining: bool,
        user: User | None,
        tracking_id: str,
        display_name: str,
        listening_language: str | None,
        new_guest_token: str | None,
        live_pts: dict,
    ) -> dict | None:
        """Return a lobby response dict if the user must wait, else None."""
        max_cap = room.settings.get("max_participants", 20)
        if len(live_pts) >= max_cap and (
            not tracking_id or tracking_id not in live_pts
        ):
            raise BadRequestException(
                code="ROOM_FULL",
                message=(
                    f"The room has reached its maximum"
                    f" capacity of {max_cap} participants."
                ),
            )

        lock_room = room.settings.get("lock_room", False)
        requires_lobby = lock_room and not is_host and not is_rejoining

        # New unauthenticated guests ALWAYS go to lobby first
        if not user and not is_rejoining:
            requires_lobby = True

        if not requires_lobby:
            return None

        # Priority: explicit join request > user profile > default "en"
        if listening_language:
            final_lang = listening_language
        elif user and user.listening_language:
            final_lang = user.listening_language
        else:
            final_lang = "en"

        await self.state.add_to_lobby(room_code, tracking_id, display_name, final_lang)

        cm = get_connection_manager()
        await cm.broadcast_to_room(
            room_code,
            {
                "type": "lobby_knock",
                "user_id": tracking_id,
                "display_name": display_name,
            },
        )

        res: dict = {"status": "waiting"}
        if new_guest_token:
            res["guest_token"] = new_guest_token
        return res

    async def _finalize_join(
        self,
        room: Room,
        room_code: str,
        *,
        ptc: Participant | None,
        user: User | None,
        tracking_id: str,
        display_name: str,
        listening_language: str | None,
        speaking_language: str | None,
        new_guest_token: str | None,
        role: str = "guest",
    ) -> dict:
        """Persist the participant record and add to Redis live state."""
        if ptc is not None:
            ptc.left_at = None
            self.repo.update_participant(ptc)
        else:
            ptc = Participant(
                room_id=room.id,
                user_id=user.id if user else None,
                guest_session_id=uuid.UUID(tracking_id) if not user else None,
                display_name=display_name,
                role=role,
            )
            self.repo.create_participant(ptc)

        # Priority: explicit join request > user profile > default "en"
        if listening_language:
            final_listen_lang = listening_language
        elif user and user.listening_language:
            final_listen_lang = user.listening_language
        else:
            final_listen_lang = "en"

        if speaking_language:
            final_speak_lang = speaking_language
        elif user and user.speaking_language:
            final_speak_lang = user.speaking_language
        else:
            final_speak_lang = "en"

        logger.info(
            (
                "JOIN: writing to Redis — room=%s tracking_id=%r "
                "listen=%s speak=%s role=%s"
            ),
            room_code,
            tracking_id,
            final_listen_lang,
            final_speak_lang,
            role,
        )
        await self.state.add_participant(
            room_code=room_code,
            user_id=tracking_id,
            language=final_listen_lang,
            speaking_language=final_speak_lang,
            display_name=display_name,
            role=role,
        )
        logger.info("JOIN: Redis write complete for tracking_id=%r", tracking_id)

        res: dict = {"status": "joined"}
        if new_guest_token:
            res["guest_token"] = new_guest_token
        return res

    # ── Main join_room entry point ────────────────────────────────────────

    async def join_room(
        self,
        room_code: str,
        user: User | None = None,
        guest_session_id: str | None = None,
        guest_name: str | None = None,
        listening_language: str | None = None,
        speaking_language: str | None = None,
    ) -> dict:
        """Handle a user joining a room.

        Orchestrates auto-activation for hosts, waiting lobby, max capacity checks,
        and Redis presence.
        Returns a dict with `status` -> "joined" or "waiting",
        and optional `guest_token`.
        """
        room = self.repo.get_room_by_code(room_code)
        if not room:
            raise NotFoundException(message="Room not found.")

        is_host = self._validate_room_for_join(room, user)

        ptc, tracking_id, display_name, new_guest_token = self._resolve_identity(
            room, user, guest_session_id, guest_name
        )
        is_rejoining = ptc is not None

        live_pts = await self.state.get_participants(room_code)

        lobby_result = await self._check_lobby_required(
            room,
            room_code,
            is_host=is_host,
            is_rejoining=is_rejoining,
            user=user,
            tracking_id=tracking_id,
            display_name=display_name,
            listening_language=listening_language,
            new_guest_token=new_guest_token,
            live_pts=live_pts,
        )
        if lobby_result is not None:
            return lobby_result

        return await self._finalize_join(
            room,
            room_code,
            ptc=ptc,
            user=user,
            tracking_id=tracking_id,
            display_name=display_name,
            listening_language=listening_language,
            speaking_language=speaking_language,
            new_guest_token=new_guest_token,
            role=ParticipantRole.HOST.value if is_host else ParticipantRole.GUEST.value,
        )

    async def leave_room(
        self,
        room_code: str,
        user: User | None = None,
        guest_session_id: str | None = None,
    ) -> None:
        """Remove user from live state and update DB record."""
        room = self.repo.get_room_by_code(room_code)
        if not room:
            return  # nothing to leave

        tracking_id = str(user.id) if user else guest_session_id
        if not tracking_id:
            return

        # 1. Redis
        await self.state.remove_participant(room_code, tracking_id)
        await self.state.remove_from_lobby(room_code, tracking_id)

        # 2. DB
        user_uuid = user.id if user else None
        guest_uuid = uuid.UUID(guest_session_id) if guest_session_id else None
        ptc = self.repo.get_participant(
            room.id, user_id=user_uuid, guest_session_id=guest_uuid
        )
        if ptc:
            ptc.left_at = utc_now()
            self.repo.update_participant(ptc)

    async def admit_user(self, host: User, room_code: str, target_user_id: str) -> None:
        """Host admits a specific user from the lobby into the active room."""
        room = self.repo.get_room_by_code(room_code)
        if not room or room.host_id != host.id:
            raise ForbiddenException(message="Only the host can admit participants.")

        was_in_lobby = await self.state.admit_from_lobby(room_code, target_user_id)

        if not was_in_lobby:
            raise BadRequestException(message="User is not in the lobby.")

    async def end_room(self, host: User, room_code: str) -> Room:
        """Host forcibly ends the meeting for everyone."""
        room = self.repo.get_room_by_code(room_code)
        if not room:
            raise NotFoundException(message="Room not found.")

        if room.host_id != host.id:
            raise ForbiddenException(message="Only the host can end the meeting.")

        # Broadcast meeting_ended BEFORE clearing Redis so the WS channel
        # still has active connections to deliver the message to.
        cm = get_connection_manager()
        await cm.broadcast_to_room(
            room_code,
            {"type": "meeting_ended"},
        )

        # Update DB status
        now = utc_now()
        room.status = RoomStatus.ENDED.value
        room.ended_at = now
        updated_room = self.repo.update_room(room)

        # Update left_at for all participants who haven't left
        self.repo.bulk_update_left_at(room.id, now)

        # Clear Redis state (after broadcast so participants were still listed)
        await self.state.cleanup_room(room_code)

        # Inject total participants and duration for the response payload
        updated_room.total_participants = self.repo.count_all_participants(room.id)  # type: ignore[attr-defined]
        assert updated_room.ended_at is not None
        delta = updated_room.ended_at - updated_room.created_at
        updated_room.duration = _format_duration(int(delta.total_seconds()))  # type: ignore[attr-defined]

        return updated_room

    def update_config(
        self, host: User, room_code: str, config: RoomConfigUpdate
    ) -> Mapped[dict[str, Any]]:
        """Host updates the room settings (partial merge)."""
        room = self.repo.get_room_by_code(room_code)
        if not room:
            raise NotFoundException(message="Room not found.")

        if room.host_id != host.id:
            raise ForbiddenException(message="Only the host can modify room settings.")

        if room.status != RoomStatus.ACTIVE.value:
            raise BadRequestException(
                code="ROOM_NOT_ACTIVE", message="Only active meetings can be modified."
            )

        update_dict = config.model_dump(exclude_unset=True)

        # We don't overwrite the whole JSON dict, we merge the updates.
        current_settings = dict(room.settings)
        current_settings.update(update_dict)

        room.settings = current_settings
        self.repo.update_room(room)

        # Ensure return type matches RoomResponse settings dict
        return room.settings  # type: ignore[return-value]

    def get_meeting_history(
        self, user_id: uuid.UUID, role_filter: str, page: int, page_size: int
    ) -> dict:
        """Returns paginated history tuple handled by repo."""
        offset = (page - 1) * page_size
        total, records = self.repo.get_meeting_history(
            user_id, role_filter, offset, page_size
        )

        items = []
        for row in records:
            items.append(
                {
                    "room_code": row.room_code,
                    "name": row.name,
                    "created_at": row.created_at,
                    "ended_at": row.ended_at,
                    "duration_minutes": row.duration_minutes,
                    "participant_count": row.participant_count,
                    "role": row.role,
                }
            )

        return {"total": total, "page": page, "page_size": page_size, "items": items}

    # ── Email Invitations ────────────────────────────────────────────────

    async def invite_participants(
        self, host: User, room_code: str, emails: list[str]
    ) -> dict:
        """Host bulk-invites participants via email."""
        room = self.repo.get_room_by_code(room_code)
        if not room:
            raise NotFoundException(message="Room not found.")

        if room.host_id != host.id:
            raise ForbiddenException(message="Only the host can send invitations.")

        email_svc = get_email_producer_service()
        sent_count = 0
        failed_emails = []

        # Meeting timeframe string for email body
        time_str = "now"
        if room.scheduled_at:
            time_str = room.scheduled_at.strftime("%B %d, %Y at %H:%M UTC")

        for email in emails:
            # Generate a secure token for the invitation link
            token = secrets.token_urlsafe(32)

            # Valid for 48 hours
            expires_at = utc_now() + timedelta(hours=48)

            invitation = MeetingInvitation(
                room_id=room.id,
                inviter_id=host.id,
                email=email,
                token=token,
                expires_at=expires_at,
            )
            self.repo.create_invitation(invitation)

            join_url = f"{settings.FRONTEND_BASE_URL}/meet/{room_code}?token={token}"

            # Use basic HTML and the existing Kafka producer to enqueue the dispatch
            try:
                await email_svc.send_email(
                    to=email,
                    subject=f"You've been invited to '{room.name}' on FluentMeet",
                    html_body=None,
                    template_data={
                        "host_name": host.full_name or host.email,
                        "room": room.name,
                        "url": join_url,
                        "time_str": time_str,
                    },
                    template="meeting_invite",
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to queue Kafka event for email to {email}: {e}")
                failed_emails.append(email)

        return {"sent": sent_count, "failed": failed_emails}
