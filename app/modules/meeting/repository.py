"""Database access layer for the meeting feature package."""

import uuid
from collections.abc import Sequence

from sqlalchemy import Row, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.modules.meeting.constants import ParticipantRole, RoomStatus
from app.modules.meeting.models import MeetingInvitation, Participant, Room


class MeetingRepository:
    """Encapsulates raw database queries for meeting models,
    separating them from business logic."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Room CRUD ────────────────────────────────────────────────────────
    def create_room(self, room: Room) -> Room:
        self.db.add(room)
        self.db.commit()
        self.db.refresh(room)
        return room

    def get_room_by_code(self, room_code: str) -> Room | None:
        return self.db.execute(
            select(Room).where(Room.room_code == room_code)
        ).scalar_one_or_none()

    def room_code_exists(self, room_code: str) -> bool:
        return (
            self.db.execute(
                select(func.count(Room.id)).where(Room.room_code == room_code)
            ).scalar_one()
            > 0
        )

    def update_room(self, room: Room) -> Room:
        self.db.commit()
        self.db.refresh(room)
        return room

    # ── Participant CRUD ─────────────────────────────────────────────────
    def create_participant(self, participant: Participant) -> Participant:
        self.db.add(participant)
        self.db.commit()
        self.db.refresh(participant)
        return participant

    def get_participant(
        self,
        room_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        guest_session_id: uuid.UUID | None = None,
    ) -> Participant | None:
        stmt = select(Participant).where(Participant.room_id == room_id)
        if user_id:
            stmt = stmt.where(Participant.user_id == user_id)
        elif guest_session_id:
            stmt = stmt.where(Participant.guest_session_id == guest_session_id)
        else:
            return None
        return self.db.execute(stmt).scalar_one_or_none()

    def update_participant(self, participant: Participant) -> Participant:
        self.db.commit()
        self.db.refresh(participant)
        return participant

    def count_all_participants(self, room_id: uuid.UUID) -> int:
        """Counts every unique participant that has ever joined the room."""
        return self.db.execute(
            select(func.count(Participant.id)).where(Participant.room_id == room_id)
        ).scalar_one()

    # ── History Queries ──────────────────────────────────────────────────
    def get_meeting_history(
        self, user_id: uuid.UUID, role_filter: str, offset: int, limit: int
    ) -> tuple[int, Sequence[Row]]:
        """
        Returns a tuple of (total_count, paginated_records) for meeting history.
        Each record matches the shape needed for `MeetingHistoryItem`.
        """
        # Base query to get room info, participant count,
        # and the role of the requesting user.
        # We join Room with Participant to see the user's
        # role in that specific room.
        base_query = (
            select(
                Room.room_code,
                Room.name,
                Room.created_at,
                Room.ended_at,
                case(
                    (
                        Room.ended_at.isnot(None),
                        func.round(
                            func.extract(
                                "epoch",
                                Room.ended_at - Room.created_at,
                            )
                            / 60
                        ),
                    ),
                    else_=None,
                ).label("duration_minutes"),
                func.count(Participant.id).label("participant_count"),
                # Subquery to get the requesting user's role in this room
                select(Participant.role)
                .where(
                    and_(Participant.room_id == Room.id, Participant.user_id == user_id)
                )
                .correlate(Room)
                .scalar_subquery()
                .label("role"),
            )
            .join(Participant, Participant.room_id == Room.id)
            .where(Room.status == RoomStatus.ENDED.value)
            .group_by(Room.id)
        )

        # Apply role filter
        if role_filter == ParticipantRole.HOST.value:
            # Only rooms where they are host
            base_query = base_query.where(Room.host_id == user_id)
        elif role_filter == ParticipantRole.GUEST.value:
            # Only rooms where they participated as a guest
            # This requires checking the participant table explicitly
            base_query = base_query.where(
                and_(
                    Room.id.in_(
                        select(Participant.room_id).where(
                            and_(
                                Participant.user_id == user_id,
                                Participant.role == ParticipantRole.GUEST.value,
                            )
                        )
                    )
                )
            )
        else:  # "all"
            # Rooms where they host OR participated
            base_query = base_query.where(
                or_(
                    Room.host_id == user_id,
                    Room.id.in_(
                        select(Participant.room_id).where(
                            Participant.user_id == user_id
                        )
                    ),
                )
            )

        # 1. Get total count
        count_query = select(func.count()).select_from(base_query.subquery())
        total = self.db.execute(count_query).scalar_one()

        # 2. Get paginated results
        paginated_query = (
            base_query.order_by(Room.ended_at.desc().nulls_last())
            .offset(offset)
            .limit(limit)
        )
        results = self.db.execute(paginated_query).all()

        return total, results

    # ── Invitations ──────────────────────────────────────────────────────
    def create_invitation(self, invitation: MeetingInvitation) -> MeetingInvitation:
        self.db.add(invitation)
        self.db.commit()
        self.db.refresh(invitation)
        return invitation

    def get_invitation_by_token(self, token: str) -> MeetingInvitation | None:
        return self.db.execute(
            select(MeetingInvitation).where(MeetingInvitation.token == token)
        ).scalar_one_or_none()
