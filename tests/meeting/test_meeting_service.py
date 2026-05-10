"""Unit tests for ``app.meeting.service.MeetingService``.

The repository and Redis state service are fully mocked so that tests
exercise pure business logic without touching a database or Redis.
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import (
    BadRequestException,
    ForbiddenException,
    InternalServerException,
    NotFoundException,
)
from app.modules.auth.models import User
from app.modules.meeting.constants import ParticipantRole, RoomStatus
from app.modules.meeting.models import Participant, Room
from app.modules.meeting.service import MeetingService, _format_duration, utc_now

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    *,
    user_id: uuid.UUID | None = None,
    email: str = "host@example.com",
    full_name: str = "Host User",
    listening_language: str = "en",
) -> MagicMock:
    user = MagicMock(spec=User)
    user.id = user_id or uuid.uuid4()
    user.email = email
    user.full_name = full_name
    user.listening_language = listening_language
    return user


def _make_room(
    *,
    host_id: uuid.UUID,
    room_code: str = "ABCDEF123456",
    status: str = RoomStatus.ACTIVE.value,
    settings: dict | None = None,
    scheduled_at: datetime | None = None,
) -> MagicMock:
    room = MagicMock(spec=Room)
    room.id = uuid.uuid4()
    room.room_code = room_code
    room.host_id = host_id
    room.name = "Test Room"
    room.status = status
    room.settings = settings or {
        "lock_room": False,
        "enable_transcription": False,
        "max_participants": 20,
    }
    room.scheduled_at = scheduled_at
    room.created_at = utc_now()
    room.ended_at = None
    return room


def _make_participant(
    *,
    room_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    guest_session_id: uuid.UUID | None = None,
    display_name: str = "A User",
    role: str = ParticipantRole.GUEST.value,
) -> MagicMock:
    ptc = MagicMock(spec=Participant)
    ptc.id = uuid.uuid4()
    ptc.room_id = room_id
    ptc.user_id = user_id
    ptc.guest_session_id = guest_session_id
    ptc.display_name = display_name
    ptc.role = role
    ptc.left_at = None
    return ptc


def _build_service() -> tuple[MeetingService, MagicMock, AsyncMock]:
    repo = MagicMock()
    state = AsyncMock()
    svc = MeetingService(repo=repo, state=state)
    return svc, repo, state


@pytest.fixture(autouse=True)
def mock_cm():
    """Mock get_connection_manager project-wide for these tests.

    This prevents real Redis connections and fixes 'Event loop is closed'
    errors on Windows/asyncio.
    """
    with patch("app.modules.meeting.service.get_connection_manager") as mock_get:
        mock_instance = MagicMock()
        mock_instance.broadcast_to_room = AsyncMock()
        mock_instance.send_to_user = AsyncMock()
        mock_get.return_value = mock_instance
        yield mock_instance


# ---------------------------------------------------------------------------
# _format_duration helper
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_seconds_only(self) -> None:
        assert _format_duration(45) == "45 seconds"

    def test_minutes_only(self) -> None:
        assert _format_duration(300) == "5 minutes"

    def test_hours_and_minutes(self) -> None:
        assert _format_duration(3900) == "1 hours, 5 minutes"


# ---------------------------------------------------------------------------
# create_room
# ---------------------------------------------------------------------------


class TestCreateRoom:
    def test_creates_room_and_host_participant(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()

        repo.room_code_exists.return_value = False
        repo.create_room.side_effect = lambda r: r
        repo.create_participant.side_effect = lambda p: p

        room = svc.create_room(
            host=host,
            name="Demo",
            room_settings=None,
            scheduled_at=None,
        )

        assert room.name == "Demo"
        assert room.host_id == host.id
        assert room.status == RoomStatus.PENDING.value
        repo.create_room.assert_called_once()
        repo.create_participant.assert_called_once()
        created_ptc = repo.create_participant.call_args[0][0]
        assert created_ptc.role == ParticipantRole.HOST.value

    def test_retries_on_room_code_collision(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()

        # First 3 codes collide, 4th is unique
        repo.room_code_exists.side_effect = [True, True, True, False]
        repo.create_room.side_effect = lambda r: r
        repo.create_participant.side_effect = lambda p: p

        room = svc.create_room(
            host=host, name="R", room_settings=None, scheduled_at=None
        )
        assert room is not None
        assert repo.room_code_exists.call_count == 4

    def test_raises_after_max_retries(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()

        repo.room_code_exists.return_value = True  # always collides

        with pytest.raises(
            InternalServerException, match="Failed to generate room code"
        ):
            svc.create_room(host=host, name="R", room_settings=None, scheduled_at=None)


# ---------------------------------------------------------------------------
# get_room_details
# ---------------------------------------------------------------------------


class TestGetRoomDetails:
    @pytest.mark.asyncio
    async def test_returns_room_with_live_count_when_active(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id, status=RoomStatus.ACTIVE.value)

        repo.get_room_by_code.return_value = room
        state.get_participants.return_value = {"u1": {}, "u2": {}}

        result = await svc.get_room_details("ABCDEF123456")

        assert result.participant_count == 2
        state.get_participants.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_db_count_when_not_active(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id, status=RoomStatus.PENDING.value)

        repo.get_room_by_code.return_value = room
        repo.count_all_participants.return_value = 5

        result = await svc.get_room_details("ABCDEF123456")

        assert result.participant_count == 5
        state.get_participants.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_not_found_for_missing_room(self) -> None:
        svc, repo, _state = _build_service()
        repo.get_room_by_code.return_value = None

        with pytest.raises(NotFoundException):
            await svc.get_room_details("INVALID")


# ---------------------------------------------------------------------------
# get_live_state
# ---------------------------------------------------------------------------


class TestGetLiveState:
    @pytest.mark.asyncio
    async def test_returns_active_and_lobby(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        state.get_participants.return_value = {"u1": {"status": "connected"}}
        state.get_lobby.return_value = {"u2": {"display_name": "Guest"}}

        result = await svc.get_live_state(user=host, room_code="ABCDEF123456")

        assert "active" in result
        assert "lobby" in result
        assert len(result["active"]) == 1
        assert len(result["lobby"]) == 1

    async def test_non_host_receives_empty_lobby(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        other_user = _make_user(email="other@example.com")
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        state.get_participants.return_value = {"u1": {"status": "connected"}}
        state.get_lobby.return_value = {"u2": {"display_name": "Guest"}}

        result = await svc.get_live_state(user=other_user, room_code="ABCDEF123456")

        assert "active" in result
        assert result["lobby"] == {}
        assert len(result["active"]) == 1

    @pytest.mark.asyncio
    async def test_raises_not_found_for_missing_room(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        repo.get_room_by_code.return_value = None

        with pytest.raises(NotFoundException):
            await svc.get_live_state(user=host, room_code="INVALID")


# ---------------------------------------------------------------------------
# join_room
# ---------------------------------------------------------------------------


class TestJoinRoom:
    @pytest.mark.asyncio
    async def test_host_joins_pending_room_activates_it(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id, status=RoomStatus.PENDING.value)

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        repo.create_participant.side_effect = lambda p: p
        state.get_participants.return_value = {}

        result = await svc.join_room(room_code="ABCDEF123456", user=host)

        assert result["status"] == "joined"
        assert room.status == RoomStatus.ACTIVE.value
        repo.update_room.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_host_cannot_join_pending_room(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        guest_user = _make_user(email="guest@example.com")
        room = _make_room(host_id=host.id, status=RoomStatus.PENDING.value)

        repo.get_room_by_code.return_value = room

        with pytest.raises(BadRequestException, match="host has not started"):
            await svc.join_room(room_code="ABCDEF123456", user=guest_user)

    @pytest.mark.asyncio
    async def test_raises_not_found_for_ended_room(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id, status=RoomStatus.ENDED.value)

        repo.get_room_by_code.return_value = room

        with pytest.raises(NotFoundException, match="already ended"):
            await svc.join_room(room_code="ABCDEF123456", user=host)

    @pytest.mark.asyncio
    async def test_raises_not_found_for_missing_room(self) -> None:
        svc, repo, _state = _build_service()
        repo.get_room_by_code.return_value = None

        with pytest.raises(NotFoundException, match="Room not found"):
            await svc.join_room(room_code="MISSING")

    @pytest.mark.asyncio
    async def test_authenticated_user_joins_active_room(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        joiner = _make_user(email="joiner@example.com")
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        repo.create_participant.side_effect = lambda p: p
        state.get_participants.return_value = {}

        result = await svc.join_room(room_code="ABCDEF123456", user=joiner)

        assert result["status"] == "joined"
        repo.create_participant.assert_called_once()
        state.add_participant.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_guest_without_name_raises(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        state.get_participants.return_value = {}

        with pytest.raises(BadRequestException, match="display_name is required"):
            await svc.join_room(
                room_code="ABCDEF123456",
                user=None,
                guest_session_id=None,
                guest_name=None,
            )

    @pytest.mark.asyncio
    async def test_new_guest_goes_to_lobby(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        state.get_participants.return_value = {}

        result = await svc.join_room(
            room_code="ABCDEF123456",
            user=None,
            guest_session_id=None,
            guest_name="Guest Bob",
        )

        assert result["status"] == "waiting"
        assert "guest_token" in result
        state.add_to_lobby.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_room_full_raises(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        joiner = _make_user(email="joiner@example.com")
        room = _make_room(
            host_id=host.id,
            settings={"lock_room": False, "max_participants": 2},
        )

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        # Room already has 2 participants
        state.get_participants.return_value = {"u1": {}, "u2": {}}

        with pytest.raises(BadRequestException, match="maximum capacity"):
            await svc.join_room(room_code="ABCDEF123456", user=joiner)

    @pytest.mark.asyncio
    async def test_rejoining_user_updates_left_at(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        joiner = _make_user(email="joiner@example.com")
        room = _make_room(host_id=host.id)

        existing_ptc = _make_participant(
            room_id=room.id, user_id=joiner.id, display_name="Joiner"
        )
        existing_ptc.left_at = utc_now()

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = existing_ptc
        state.get_participants.return_value = {}

        result = await svc.join_room(room_code="ABCDEF123456", user=joiner)

        assert result["status"] == "joined"
        assert existing_ptc.left_at is None
        repo.update_participant.assert_called_once()
        # Should NOT create a new participant
        repo.create_participant.assert_not_called()

    @pytest.mark.asyncio
    async def test_locked_room_sends_authenticated_guest_to_lobby(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        joiner = _make_user(email="joiner@example.com")
        room = _make_room(
            host_id=host.id,
            settings={"lock_room": True, "max_participants": 20},
        )

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        state.get_participants.return_value = {}

        result = await svc.join_room(room_code="ABCDEF123456", user=joiner)

        assert result["status"] == "waiting"
        state.add_to_lobby.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_host_bypasses_locked_room_lobby(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(
            host_id=host.id,
            status=RoomStatus.PENDING.value,
            settings={"lock_room": True, "max_participants": 20},
        )

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = None
        repo.create_participant.side_effect = lambda p: p
        state.get_participants.return_value = {}

        result = await svc.join_room(room_code="ABCDEF123456", user=host)

        assert result["status"] == "joined"
        state.add_to_lobby.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_scheduled_future_room_rejects_non_host(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        joiner = _make_user(email="joiner@example.com")
        future = utc_now() + timedelta(hours=1)
        room = _make_room(
            host_id=host.id,
            status=RoomStatus.PENDING.value,
            scheduled_at=future,
        )

        repo.get_room_by_code.return_value = room

        with pytest.raises(BadRequestException, match="scheduled for a future time"):
            await svc.join_room(room_code="ABCDEF123456", user=joiner)

    @pytest.mark.asyncio
    async def test_existing_participant_in_full_room_can_rejoin(self) -> None:
        """A user already in the live_pts dict should not be blocked by capacity."""
        svc, repo, state = _build_service()
        host = _make_user()
        joiner = _make_user(email="joiner@example.com")
        room = _make_room(
            host_id=host.id,
            settings={"lock_room": False, "max_participants": 2},
        )

        existing_ptc = _make_participant(
            room_id=room.id, user_id=joiner.id, display_name="Joiner"
        )

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = existing_ptc
        # Room has 2 participants, including the joiner
        state.get_participants.return_value = {
            str(joiner.id): {},
            "other_user": {},
        }

        result = await svc.join_room(room_code="ABCDEF123456", user=joiner)
        assert result["status"] == "joined"


# ---------------------------------------------------------------------------
# leave_room
# ---------------------------------------------------------------------------


class TestLeaveRoom:
    @pytest.mark.asyncio
    async def test_user_leaves_room(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        leaver = _make_user(email="leaver@example.com")
        room = _make_room(host_id=host.id)

        ptc = _make_participant(room_id=room.id, user_id=leaver.id)

        repo.get_room_by_code.return_value = room
        repo.get_participant.return_value = ptc

        await svc.leave_room(room_code="ABCDEF123456", user=leaver)

        state.remove_participant.assert_awaited_once()
        state.remove_from_lobby.assert_awaited_once()
        assert ptc.left_at is not None
        repo.update_participant.assert_called_once()

    @pytest.mark.anyio
    async def test_leave_nonexistent_room_is_noop(self) -> None:
        svc, repo, _state = _build_service()
        repo.get_room_by_code.return_value = None

        # Should not raise
        await svc.leave_room(room_code="NOPE", user=_make_user())

    @pytest.mark.anyio
    async def test_leave_without_tracking_id_is_noop(self) -> None:
        svc, repo, state = _build_service()
        room = _make_room(host_id=uuid.uuid4())
        repo.get_room_by_code.return_value = room

        await svc.leave_room(room_code="ABCDEF123456", user=None, guest_session_id=None)

        state.remove_participant.assert_not_awaited()


# ---------------------------------------------------------------------------
# admit_user
# ---------------------------------------------------------------------------


class TestAdmitUser:
    @pytest.mark.anyio
    async def test_host_admits_user_from_lobby(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        state.get_lobby.return_value = {
            "u99": {"display_name": "Guest Bob", "language": "en"}
        }
        state.admit_from_lobby.return_value = True

        await svc.admit_user(host=host, room_code="ABCDEF123456", target_user_id="u99")

        state.admit_from_lobby.assert_awaited_once_with("ABCDEF123456", "u99")

    @pytest.mark.anyio
    async def test_non_host_cannot_admit(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        non_host = _make_user(email="nohost@example.com")
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room

        with pytest.raises(ForbiddenException, match="Only the host"):
            await svc.admit_user(
                host=non_host, room_code="ABCDEF123456", target_user_id="u99"
            )

    @pytest.mark.anyio
    async def test_admit_user_not_in_lobby_raises(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        state.get_lobby.return_value = {}
        state.admit_from_lobby.return_value = False

        with pytest.raises(BadRequestException, match="not in the lobby"):
            await svc.admit_user(
                host=host, room_code="ABCDEF123456", target_user_id="u99"
            )


# ---------------------------------------------------------------------------
# end_room
# ---------------------------------------------------------------------------


class TestEndRoom:
    @pytest.mark.anyio
    async def test_host_ends_room(self) -> None:
        svc, repo, state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)
        room.created_at = utc_now() - timedelta(minutes=30)

        repo.get_room_by_code.return_value = room
        repo.update_room.side_effect = lambda r: r
        repo.count_all_participants.return_value = 3

        result = await svc.end_room(host=host, room_code="ABCDEF123456")

        assert result.status == RoomStatus.ENDED.value
        assert result.ended_at is not None
        assert result.total_participants == 3
        assert result.duration is not None
        state.cleanup_room.assert_awaited_once_with("ABCDEF123456")

    @pytest.mark.anyio
    async def test_non_host_cannot_end_room(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        other = _make_user(email="other@example.com")
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room

        with pytest.raises(ForbiddenException, match="Only the host"):
            await svc.end_room(host=other, room_code="ABCDEF123456")

    @pytest.mark.anyio
    async def test_end_missing_room_raises(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        repo.get_room_by_code.return_value = None

        with pytest.raises(NotFoundException):
            await svc.end_room(host=host, room_code="MISSING")


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_host_updates_config(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)
        room.settings = {"lock_room": False, "max_participants": 20}

        repo.get_room_by_code.return_value = room

        config = MagicMock()
        config.model_dump.return_value = {"lock_room": True}

        result = svc.update_config(host=host, room_code="ABCDEF123456", config=config)

        assert result["lock_room"] is True
        assert result["max_participants"] == 20
        repo.update_room.assert_called_once()

    def test_non_host_cannot_update_config(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        other = _make_user(email="other@example.com")
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room

        config = MagicMock()
        config.model_dump.return_value = {"lock_room": True}

        with pytest.raises(ForbiddenException):
            svc.update_config(host=other, room_code="ABCDEF123456", config=config)

    def test_cannot_update_ended_room(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id, status=RoomStatus.ENDED.value)

        repo.get_room_by_code.return_value = room

        config = MagicMock()
        config.model_dump.return_value = {"lock_room": True}

        with pytest.raises(
            BadRequestException, match="Only active meetings can be modified"
        ):
            svc.update_config(host=host, room_code="ABCDEF123456", config=config)

    def test_missing_room_raises(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        repo.get_room_by_code.return_value = None

        with pytest.raises(NotFoundException):
            svc.update_config(host=host, room_code="X", config=MagicMock())


# ---------------------------------------------------------------------------
# get_meeting_history
# ---------------------------------------------------------------------------


class TestGetMeetingHistory:
    def test_returns_paginated_structure(self) -> None:
        svc, repo, _state = _build_service()
        uid = uuid.uuid4()

        fake_row = MagicMock()
        fake_row.room_code = "ABC"
        fake_row.name = "Room"
        fake_row.created_at = utc_now()
        fake_row.ended_at = utc_now()
        fake_row.duration_minutes = 30
        fake_row.participant_count = 5
        fake_row.role = "host"

        repo.get_meeting_history.return_value = (1, [fake_row])

        result = svc.get_meeting_history(
            user_id=uid, role_filter="all", page=1, page_size=20
        )

        assert result["total"] == 1
        assert result["page"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["room_code"] == "ABC"


# ---------------------------------------------------------------------------
# invite_participants
# ---------------------------------------------------------------------------


class TestInviteParticipants:
    @pytest.mark.anyio
    async def test_sends_invitations(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        repo.create_invitation.side_effect = lambda inv: inv

        mock_email_svc = AsyncMock()
        mock_email_svc.send_email = AsyncMock()

        with patch(
            "app.modules.meeting.service.get_email_producer_service",
            return_value=mock_email_svc,
        ):
            result = await svc.invite_participants(
                host=host,
                room_code="ABCDEF123456",
                emails=["a@b.com", "c@d.com"],
            )

        assert result["sent"] == 2
        assert result["failed"] == []
        assert mock_email_svc.send_email.await_count == 2

    @pytest.mark.anyio
    async def test_non_host_cannot_invite(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        other = _make_user(email="other@example.com")
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room

        with pytest.raises(ForbiddenException, match="Only the host"):
            await svc.invite_participants(
                host=other, room_code="ABCDEF123456", emails=["a@b.com"]
            )

    @pytest.mark.anyio
    async def test_invite_missing_room_raises(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        repo.get_room_by_code.return_value = None

        with pytest.raises(NotFoundException):
            await svc.invite_participants(
                host=host, room_code="MISSING", emails=["a@b.com"]
            )

    @pytest.mark.anyio
    async def test_failed_email_captured(self) -> None:
        svc, repo, _state = _build_service()
        host = _make_user()
        room = _make_room(host_id=host.id)

        repo.get_room_by_code.return_value = room
        repo.create_invitation.side_effect = lambda inv: inv

        mock_email_svc = AsyncMock()
        mock_email_svc.send_email = AsyncMock(side_effect=Exception("Kafka down"))

        with patch(
            "app.modules.meeting.service.get_email_producer_service",
            return_value=mock_email_svc,
        ):
            result = await svc.invite_participants(
                host=host,
                room_code="ABCDEF123456",
                emails=["fail@test.com"],
            )

        assert result["sent"] == 0
        assert result["failed"] == ["fail@test.com"]
