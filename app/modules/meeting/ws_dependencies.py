"""WebSocket-specific dependencies for authentication and authorization.

WebSockets in the browser do not support sending custom headers easily.
Instead, we pass the JWT as a query parameter (`?token=...`). These
dependencies validate the token before the connection upgrade completes.
"""

import logging

from fastapi import Depends, Query, WebSocketException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.meeting.state import MeetingStateService

logger = logging.getLogger(__name__)


def authenticate_ws(token: str = Query(...), db: Session = Depends(get_db)) -> str:
    """Validate the provided JWT token for a WebSocket connection.

    Works for both Authenticated Users (who present an access token)
    and Guests (who present a guest token).

    Returns:
        The user ID (UUID string) or guest session ID extracted from the token.
    """
    error_exc = WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION,
        reason="Invalid or missing authentication token",
    )

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError as err:
        raise error_exc from err

    raw_sub = payload.get("sub")
    token_type = payload.get("type", "access")

    if (
        not raw_sub
        or not isinstance(raw_sub, str)
        or token_type not in ("access", "guest")
    ):
        raise error_exc

    if token_type == "access":
        # The 'sub' is an email; we need the UUID to match Redis participant state
        user = db.execute(
            select(User).where(User.email == raw_sub)
        ).scalar_one_or_none()
        if not user:
            raise error_exc
        resolved_id = str(user.id)
        return resolved_id

    return str(raw_sub)


async def assert_room_participant(room_code: str, user_id: str) -> dict:
    """Ensure the user has successfully joined the room.

    Checks the Redis active participant list managed by MeetingStateService.
    If the user has not called POST /meetings/{room}/join, they cannot
    connect to the WebSockets.

    Returns:
        The participant state dictionary (e.g. ``{"language": "en"}``).
    """
    state_service = MeetingStateService()
    participants = await state_service.get_participants(room_code)

    participant_state = participants.get(user_id)
    if not participant_state:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="User is not a participant of this room",
        )
    return participant_state
