"""Pydantic schemas for the meeting feature package."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── Request schemas ───────────────────────────────────────────────────


class RoomSettings(BaseModel):
    lock_room: bool | None = Field(
        default=None, description="Require host to admit guests."
    )
    enable_transcription: bool | None = Field(
        default=None, description="Enable live transcription."
    )
    max_participants: int | None = Field(
        default=None, ge=2, le=100, description="Max participants limit."
    )


class RoomCreate(BaseModel):
    name: str = Field(..., max_length=255, description="Name of the meeting room.")
    settings: RoomSettings | None = None
    scheduled_at: datetime | None = Field(
        default=None, description="Optional future datetime for scheduled meetings."
    )


class RoomConfigUpdate(RoomSettings):
    """Schema for updating a room's configuration."""

    pass


class InviteRequest(BaseModel):
    emails: list[EmailStr] = Field(
        ..., max_length=20, description="List of emails to invite."
    )


class JoinRoomRequest(BaseModel):
    display_name: str | None = Field(
        default=None,
        description=(
            "Required for guests. Authenticated users will use their account name."
        ),
    )
    listening_language: str | None = Field(
        default=None,
        description="Language for receiving translations. "
        "Falls back to user profile language if not set.",
    )
    speaking_language: str | None = Field(
        default=None,
        description="Language the participant will speak. "
        "Used for STT source language selection.",
    )


# ── Response schemas ──────────────────────────────────────────────────


class RoomResponse(BaseModel):
    room_code: str
    name: str
    host_id: uuid.UUID
    status: str
    settings: dict
    scheduled_at: datetime | None = None
    created_at: datetime
    ended_at: datetime | None = None
    join_url: str | None = None  # Populated dynamically by the service
    participant_count: int | None = None  # Populated dynamically by the service
    total_participants: int | None = None
    duration: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ParticipantResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str | None = None
    role: str
    joined_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MeetingHistoryItem(BaseModel):
    room_code: str
    name: str
    created_at: datetime
    ended_at: datetime | None = None
    duration_minutes: int | None = None
    participant_count: int
    role: str


class PaginatedMeetingHistory(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MeetingHistoryItem]


class InviteResponse(BaseModel):
    sent: int
    failed: list[str]


# ── Envelope schemas ──────────────────────────────────────────────────


class RoomApiResponse(BaseModel):
    status_code: int = 200
    status: str = "success"
    message: str
    data: RoomResponse


class MeetingHistoryApiResponse(BaseModel):
    status_code: int = 200
    status: str = "success"
    message: str
    data: PaginatedMeetingHistory


class InviteApiResponse(BaseModel):
    status_code: int = 200
    status: str = "success"
    message: str
    data: InviteResponse
