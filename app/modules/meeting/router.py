"""REST API endpoints for the meeting feature package."""

import logging

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from jose import jwt

from app.core.config import settings
from app.core.dependencies import get_current_user, get_current_user_optional
from app.modules.auth.models import User
from app.modules.meeting.constants import (
    MSG_INVITATIONS_SENT,
    MSG_MEETING_ENDED,
    MSG_MEETING_HISTORY,
    MSG_ROOM_CONFIG_UPDATED,
    MSG_ROOM_CREATED,
    MSG_ROOM_DETAILS,
    MSG_ROOM_JOINED,
    MSG_ROOM_LEFT,
    MSG_USER_ADMITTED,
)
from app.modules.meeting.dependencies import get_meeting_service
from app.modules.meeting.schemas import (
    InviteApiResponse,
    InviteRequest,
    JoinRoomRequest,
    MeetingHistoryApiResponse,
    RoomApiResponse,
    RoomConfigUpdate,
    RoomCreate,
    RoomResponse,
)
from app.modules.meeting.service import MeetingService
from app.services.connection_manager import get_connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meetings"])


def extract_guest_session(request: Request) -> str | None:
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.split(" ")[1]
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
            if payload.get("type") == "guest":
                return payload.get("sub")  # type: ignore[no-any-return]
        except Exception as exc:
            logger.error(f"Extract guest session error: {exc}")
            pass
    return None


@router.post(
    "/",
    response_model=RoomApiResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new meeting room",
)
async def create_room(
    payload: RoomCreate,
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> RoomApiResponse:
    room = service.create_room(
        host=current_user,
        name=payload.name,
        room_settings=payload.settings,
        scheduled_at=payload.scheduled_at,
    )
    return RoomApiResponse(
        status_code=status.HTTP_201_CREATED,
        message=MSG_ROOM_CREATED,
        data=RoomResponse.model_validate(room),
    )


@router.get(
    "/history",
    response_model=MeetingHistoryApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Get paginated history of meetings",
)
async def get_history(
    role: str = Query(
        "all",
        description="Filter by role: host, guest, or all",
        pattern="^(host|guest|all)$",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> MeetingHistoryApiResponse:
    history_data = service.get_meeting_history(
        user_id=current_user.id, role_filter=role, page=page, page_size=page_size
    )
    return MeetingHistoryApiResponse(
        message=MSG_MEETING_HISTORY,
        data=history_data,  # type: ignore[arg-type]
    )


@router.get(
    "/{room_code}",
    response_model=RoomApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Get room details including live participant count",
)
async def get_room(
    room_code: str,
    _current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> RoomApiResponse:
    room = await service.get_room_details(room_code)
    return RoomApiResponse(
        message=MSG_ROOM_DETAILS,
        data=RoomResponse.model_validate(room),
    )


@router.get(
    "/{room_code}/participants",
    status_code=status.HTTP_200_OK,
    summary="Get live active participants and lobby waiting list (Host only)",
)
async def get_live_state(
    room_code: str,
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> JSONResponse:
    state = await service.get_live_state(host=current_user, room_code=room_code)
    return JSONResponse(
        content={
            "status": "success",
            "message": "Live room state retrieved",
            "data": state,
        },
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{room_code}/join",
    status_code=status.HTTP_200_OK,
    summary="Join a room or enter the lobby",
)
async def join_room(
    room_code: str,
    request: Request,
    payload: JoinRoomRequest | None = None,
    current_user: User | None = Depends(get_current_user_optional),
    service: MeetingService = Depends(get_meeting_service),
) -> JSONResponse:
    guest_session_id = extract_guest_session(request)
    result = await service.join_room(
        room_code=room_code,
        user=current_user,
        guest_session_id=guest_session_id,
        guest_name=payload.display_name if payload else None,
        listening_language=payload.listening_language if payload else None,
        speaking_language=payload.speaking_language if payload else None,
    )
    return JSONResponse(
        content={"status": "success", "message": MSG_ROOM_JOINED, "data": result},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{room_code}/leave",
    status_code=status.HTTP_200_OK,
    summary="Leave the active room or lobby",
)
async def leave_room(
    room_code: str,
    request: Request,
    current_user: User | None = Depends(get_current_user_optional),
    service: MeetingService = Depends(get_meeting_service),
) -> JSONResponse:
    guest_session_id = extract_guest_session(request)
    await service.leave_room(
        room_code=room_code, user=current_user, guest_session_id=guest_session_id
    )
    return JSONResponse(
        content={"status": "success", "message": MSG_ROOM_LEFT},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{room_code}/admit/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Host proxy: admits a user from the waiting room lobby",
)
async def admit_user(
    room_code: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> JSONResponse:
    await service.admit_user(
        host=current_user, room_code=room_code, target_user_id=user_id
    )
    return JSONResponse(
        content={"status": "success", "message": MSG_USER_ADMITTED},
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{room_code}/end",
    response_model=RoomApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Host forcibly ends the meeting room.",
)
async def end_room(
    room_code: str,
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> RoomApiResponse:
    room = await service.end_room(host=current_user, room_code=room_code)
    return RoomApiResponse(
        message=MSG_MEETING_ENDED,
        data=RoomResponse.model_validate(room),
    )


@router.patch(
    "/{room_code}/config",
    status_code=status.HTTP_200_OK,
    summary="Host partial update of room settings",
)
async def update_config(
    room_code: str,
    payload: RoomConfigUpdate,
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> JSONResponse:
    settings_data = service.update_config(
        host=current_user, room_code=room_code, config=payload
    )

    manager = get_connection_manager()
    await manager.broadcast_to_room(
        room_code, {"event": "room_config_updated", "settings": settings_data}
    )

    return JSONResponse(
        content={
            "status": "success",
            "message": MSG_ROOM_CONFIG_UPDATED,
            "data": {"settings": settings_data},
        },
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/{room_code}/invite",
    response_model=InviteApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Host invites multiple emails to a meeting via email.",
)
async def invite_participants(
    room_code: str,
    payload: InviteRequest,
    current_user: User = Depends(get_current_user),
    service: MeetingService = Depends(get_meeting_service),
) -> InviteApiResponse:
    result = await service.invite_participants(
        host=current_user, room_code=room_code, emails=payload.emails
    )
    return InviteApiResponse(
        message=MSG_INVITATIONS_SENT,
        data=result,  # type: ignore[arg-type]
    )
