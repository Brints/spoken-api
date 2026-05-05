"""Authentication Database Models module."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.modules.auth.constants import UserRole


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """Database model tracking all identity, profiles, and state constructs
    for individuals natively.

    Attributes:
        id: Primary UUID.
        email: Unique user email address identifying accounts.
        hashed_password: Encrypted payload statically parsed securely.
        full_name: Standardized user provided string.
        is_active: Activation mapping bounding sessions dynamically.
        is_verified: Identity validation marker defining login allowance.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, index=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Profile
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    google_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )

    # Language preferences
    speaking_language: Mapped[str] = mapped_column(String(10), default="en")
    listening_language: Mapped[str] = mapped_column(String(10), default="en")

    # Role
    user_role: Mapped[str] = mapped_column(
        String(50),
        default=UserRole.USER.value,
        server_default=UserRole.USER.value,
        index=True,
    )


def default_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=24)


class VerificationToken(Base):
    """Model representing a verification token for email verification or password reset.

    Attributes:
        id (uuid.UUID): Primary key identifier for the token.
        user_id (uuid.UUID): Foreign key referencing the associated user.
        token (str): Unique token string used for verification.
        expires_at (datetime): Timestamp indicating when the token expires.
        created_at (datetime): Timestamp indicating when the token was created.
    """

    __tablename__ = "verification_tokens"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, index=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=default_expiry
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PasswordResetToken(Base):
    """Model representing a password reset token.

    Attributes:
        id (uuid.UUID): Primary key identifier for the token.
        user_id (uuid.UUID): Foreign key referencing the associated user.
        token (str): Unique token string used for password reset.
        expires_at (datetime): Timestamp indicating when the token expires.
        created_at (datetime): Timestamp indicating when the token was created.
    """

    __tablename__ = "password_reset_tokens"
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, index=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=False,
        default=lambda: str(uuid.uuid4()),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
