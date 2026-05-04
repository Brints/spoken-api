import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, WebSocketException, status

from app.modules.meeting.ws_dependencies import assert_room_participant, authenticate_ws
from app.services.connection_manager import ConnectionManager


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.publish = AsyncMock()

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()

    async def mock_listen():
        if False:
            yield

    pubsub.listen.side_effect = mock_listen
    redis.pubsub.return_value = pubsub
    return redis


@pytest.fixture
def connection_manager(mock_redis):
    return ConnectionManager(mock_redis)


@pytest.mark.asyncio
async def test_connection_manager_connect(connection_manager):
    ws = MagicMock(spec=WebSocket)

    await connection_manager.connect("room1", "user1", ws)

    assert "room1" in connection_manager.active_connections
    assert connection_manager.active_connections["room1"]["user1"] == ws
    assert "room1" in connection_manager._pubsub_tasks


@pytest.mark.asyncio
async def test_connection_manager_disconnect(connection_manager):
    ws = MagicMock(spec=WebSocket)
    ws.accept = AsyncMock()

    await connection_manager.connect("room1", "user1", ws)
    connection_manager.disconnect("room1", "user1")

    assert "room1" not in connection_manager.active_connections
    assert "room1" not in connection_manager._pubsub_tasks  # task is cancelled


@pytest.mark.asyncio
async def test_connection_manager_broadcast(connection_manager, mock_redis):
    await connection_manager.broadcast_to_room("room1", {"hello": "world"}, "sender1")

    mock_redis.publish.assert_called_once()
    args, _ = mock_redis.publish.call_args
    assert args[0] == "ws:room:room1"

    payload = json.loads(args[1])
    assert payload["type"] == "broadcast"
    assert payload["sender_id"] == "sender1"
    assert payload["data"] == {"hello": "world"}


@pytest.mark.asyncio
async def test_connection_manager_unicast(connection_manager, mock_redis):
    await connection_manager.send_to_user("room1", "target2", {"hello": "world"})

    mock_redis.publish.assert_called_once()
    args, _ = mock_redis.publish.call_args
    assert args[0] == "ws:room:room1"

    payload = json.loads(args[1])
    assert payload["type"] == "unicast"
    assert payload["target_user_id"] == "target2"


@pytest.mark.asyncio
async def test_authenticate_ws_valid_token():
    with patch("app.modules.meeting.ws_dependencies.jwt.decode") as mock_decode:
        mock_decode.return_value = {"sub": "user123", "type": "guest"}

        user_id = authenticate_ws("valid_token", db=MagicMock())
        assert user_id == "user123"


@pytest.mark.asyncio
async def test_authenticate_ws_invalid_token():
    from jose import JWTError

    with patch(
        "app.modules.meeting.ws_dependencies.jwt.decode",
        side_effect=JWTError("Invalid"),
    ):
        with pytest.raises(WebSocketException) as exc:
            authenticate_ws("invalid_token", db=MagicMock())

        assert exc.value.code == status.WS_1008_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_assert_room_participant_valid():
    with patch(
        "app.modules.meeting.ws_dependencies.MeetingStateService"
    ) as mock_service_class:
        mock_service = MagicMock()
        mock_service.get_participants = AsyncMock(
            return_value={"user1": {"language": "es"}}
        )
        mock_service_class.return_value = mock_service

        state = await assert_room_participant("room1", "user1")
        assert state == {"language": "es"}


@pytest.mark.asyncio
async def test_assert_room_participant_invalid():
    with patch(
        "app.modules.meeting.ws_dependencies.MeetingStateService"
    ) as mock_service_class:
        mock_service = MagicMock()
        mock_service.get_participants = AsyncMock(
            return_value={"user2": {"language": "fr"}}
        )
        mock_service_class.return_value = mock_service

        with pytest.raises(WebSocketException) as exc:
            await assert_room_participant("room1", "user1")

        assert exc.value.code == status.WS_1008_POLICY_VIOLATION
