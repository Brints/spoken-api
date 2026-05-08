"""Database access layer for the meeting feature package."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from app.modules.meeting.constants import ParticipantRole, RoomStatus
from app.modules.meeting.models import MeetingInvitation, Participant, Room


class MeetingRepository:
    """Encapsulates raw database queries for meeting models,
    separating them from business logic."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _duration_minutes_expression(self) -> Any:
        """Build a DB-specific duration expression in minutes.

        SQLite uses julianday; PostgreSQL uses epoch extraction from interval.
        """
        dialect_name = (
            self.db.bind.dialect.name if self.db.bind is not None else ""
        ).lower()

        if dialect_name == "postgresql":
            return func.round(
                func.extract("epoch", Room.ended_at - Room.created_at) / 60.0
            )

        # Default to SQLite-compatible expression used in tests.
        return func.round(
            (func.julianday(Room.ended_at) - func.julianday(Room.created_at)) * 1440
        )

    # ── Room CRUD ────────────────────────────────────────────────────────
    def create_room(self, room: Room) -> Room:
        """Store a new room boundary natively committing securely.

        Args:
            room (Room): Native Pydantic validation mapping cast to SQLAlchemy
                construct securely.

        Returns:
            Room: Refreshed db entity returning primary identifiers dynamically
                generated natively.
        """
        self.db.add(room)
        self.db.commit()
        self.db.refresh(room)
        return room

    def get_room_by_code(self, room_code: str) -> Room | None:
        """Filter explicit Room entities actively running strings directly to database
        clauses securely.

        Args:
            room_code (str): Formatted public URL tracking token string.

        Returns:
            Room | None: Retrieved database definition natively.
        """
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

    def bulk_update_left_at(self, room_id: uuid.UUID, left_at_time: datetime) -> None:
        """Sets left_at for all participants in a room who haven't left yet."""
        stmt = (
            update(Participant)
            .where(
                and_(
                    Participant.room_id == room_id,
                    Participant.left_at.is_(None),
                )
            )
            .values(left_at=left_at_time)
        )
        self.db.execute(stmt)
        self.db.commit()

    def count_all_participants(self, room_id: uuid.UUID) -> int:
        """Counts every unique participant that has ever joined the room.

        Args:
            room_id (uuid.UUID): Identity mapping targeting specific bounds naturally.

        Returns:
            int: Total aggregations dynamically returned securely natively.
        """
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
        _duration_minutes_expr = self._duration_minutes_expression()

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
