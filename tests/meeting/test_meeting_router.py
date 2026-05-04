"""Integration tests for the meeting API router.

Uses an in-memory SQLite database, FakeRedis, and the FastAPI TestClient
to exercise full request → response cycles through ``/api/v1/meetings``.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.rate_limiter import limiter
from app.core.security import SecurityService
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.modules.auth.account_lockout import (
    AccountLockoutService,
    get_account_lockout_service,
)
from app.modules.auth.models import User
from app.modules.auth.token_store import (
    TokenStoreService,
    get_token_store_service,
)
from app.modules.meeting.dependencies import get_meeting_state_service
from app.modules.meeting.state import MeetingStateService
from app.services.email_producer import get_email_producer_service

# ---------------------------------------------------------------------------
# Fake Redis
# ---------------------------------------------------------------------------


class FakeRedis:
    """In-memory stand-in for ``redis.asyncio.Redis``."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    # -- String commands --
    async def set(
        self,
        key: str | None = None,
        value: str = "",
        ex: int | None = None,  # noqa: ARG002
        *,
        name: str | None = None,
    ) -> None:
        final_key = name or key
        self._store[final_key] = value

    async def get(
        self, key: str | None = None, *, name: str | None = None
    ) -> str | None:
        final_key = name or key
        return self._store.get(final_key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)
            self._hashes.pop(key, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self._store or key in self._hashes else 0

    async def incr(self, key: str) -> int:
        current = int(self._store.get(key, "0"))
        current += 1
        self._store[key] = str(current)
        return current

    async def scan(
        self,
        cursor: int,  # noqa: ARG002
        match: str | None = None,
        count: int | None = None,  # noqa: ARG002
    ) -> tuple[int, list[str]]:
        import fnmatch

        all_keys = list(self._store.keys()) + list(self._hashes.keys())
        matched = (
            [k for k in all_keys if fnmatch.fnmatch(k, match)] if match else all_keys
        )
        return 0, matched

    # -- Hash commands --
    async def hset(
        self,
        name: str = "",
        key: str = "",
        value: str = "",
    ) -> int:
        if name not in self._hashes:
            self._hashes[name] = {}
        self._hashes[name][key] = value
        return 1

    async def hdel(self, name: str, *keys: str) -> int:
        if name not in self._hashes:
            return 0
        count = 0
        for key in keys:
            if key in self._hashes[name]:
                del self._hashes[name][key]
                count += 1
        return count

    async def hget(self, name: str, key: str) -> str | None:
        return self._hashes.get(name, {}).get(key)

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))

    def pipeline(self) -> "FakePipeline":
        return FakePipeline(self)

    def reset(self) -> None:
        self._store.clear()
        self._hashes.clear()


class FakePipeline:
    """Minimal pipeline stand-in."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def delete(self, key: str) -> "FakePipeline":
        self._ops.append(("delete", (key,)))
        return self

    def hdel(self, name: str, *keys: str) -> "FakePipeline":
        self._ops.append(("hdel", (name, *keys)))
        return self

    def hset(self, *, name: str, key: str, value: str) -> "FakePipeline":
        self._ops.append(("hset", (name, key, value)))
        return self

    async def execute(self) -> list[int]:
        results = []
        for op, args in self._ops:
            if op == "delete":
                self._redis._store.pop(args[0], None)
                self._redis._hashes.pop(args[0], None)
                results.append(1)
            elif op == "hdel":
                name = args[0]
                keys = args[1:]
                count = 0
                if name in self._redis._hashes:
                    for k in keys:
                        if k in self._redis._hashes[name]:
                            del self._redis._hashes[name][k]
                            count += 1
                results.append(count)
            elif op == "hset":
                name, key, value = args
                if name not in self._redis._hashes:
                    self._redis._hashes[name] = {}
                self._redis._hashes[name][key] = value
                results.append(1)
        return results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def email_producer_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.send_email = AsyncMock()
    return mock


@pytest.fixture
def mock_connection_manager() -> AsyncMock:
    mock = AsyncMock()
    mock.broadcast_to_room = AsyncMock()
    mock.send_to_user = AsyncMock()
    mock.connect = AsyncMock()
    mock.disconnect = MagicMock()
    return mock


@pytest.fixture
def token_store(fake_redis: FakeRedis) -> TokenStoreService:
    return TokenStoreService(redis_client=fake_redis)  # type: ignore[arg-type]


@pytest.fixture
def meeting_state(fake_redis: FakeRedis) -> MeetingStateService:
    return MeetingStateService(redis_client=fake_redis)  # type: ignore[arg-type]


@pytest.fixture
def lockout_svc(fake_redis: FakeRedis) -> AccountLockoutService:
    return AccountLockoutService(redis_client=fake_redis)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def client(
    db_session: Session,
    email_producer_mock: AsyncMock,
    token_store: TokenStoreService,
    meeting_state: MeetingStateService,
    lockout_svc: AccountLockoutService,
    mock_connection_manager: AsyncMock,
) -> httpx.AsyncClient:
    def _override_get_db() -> Generator[Session, None, None]:
        yield db_session

    def _override_email_producer() -> AsyncMock:
        return email_producer_mock

    def _override_token_store() -> TokenStoreService:
        return token_store

    def _override_meeting_state() -> MeetingStateService:
        return meeting_state

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_email_producer_service] = _override_email_producer
    app.dependency_overrides[get_token_store_service] = _override_token_store
    app.dependency_overrides[get_meeting_state_service] = _override_meeting_state

    def _override_lockout_svc() -> AccountLockoutService:
        return lockout_svc

    app.dependency_overrides[get_account_lockout_service] = _override_lockout_svc

    from app.services.connection_manager import get_connection_manager

    def _override_connection_manager() -> AsyncMock:
        return mock_connection_manager

    app.dependency_overrides[get_connection_manager] = _override_connection_manager

    import app.modules.meeting.router as router_module
    import app.modules.meeting.service as service_module

    router_module.get_connection_manager = _override_connection_manager
    service_module.get_connection_manager = _override_connection_manager

    # Mock the kafka manager to prevent lifespan from bridging actual sockets
    import app.main as app_main_module

    mock_kafka = AsyncMock()
    app_main_module.get_kafka_manager = lambda: mock_kafka

    limiter.enabled = False
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as async_client:
        yield async_client
    limiter.enabled = True
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_user(
    db: Session,
    *,
    email: str = "host@example.com",
    password: str = "MyStr0ngP@ss!",
    full_name: str = "Test Host",
) -> User:
    svc = SecurityService()
    user = User(
        email=email.lower(),
        hashed_password=svc.hash_password(password),
        full_name=full_name,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


async def _login(
    client: httpx.AsyncClient,
    email: str = "host@example.com",
    password: str = "MyStr0ngP@ss!",
) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_room_via_api(
    client: httpx.AsyncClient,
    token: str,
    name: str = "My Room",
) -> dict:
    resp = await client.post(
        "/api/v1/meetings/",
        json={"name": name},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 201
    return resp.json()["data"]


# ---------------------------------------------------------------------------
# Test: Create Room
# ---------------------------------------------------------------------------


class TestCreateRoomRoute:
    @pytest.mark.asyncio
    async def test_creates_room_successfully(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        user = _seed_user(db_session)
        token = await _login(client)

        resp = await client.post(
            "/api/v1/meetings/",
            json={"name": "Team Standup"},
            headers=_auth_headers(token),
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "success"
        assert body["data"]["name"] == "Team Standup"
        assert body["data"]["status"] == "pending"
        assert body["data"]["host_id"] == str(user.id)
        assert body["data"]["join_url"] is not None

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/meetings/",
            json={"name": "X"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Test: Get Room Details
# ---------------------------------------------------------------------------


class TestGetRoomRoute:
    @pytest.mark.asyncio
    async def test_get_room_details(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        resp = await client.get(
            f"/api/v1/meetings/{room_code}",
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["room_code"] == room_code

    @pytest.mark.asyncio
    async def test_get_nonexistent_room_returns_404(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        resp = await client.get(
            "/api/v1/meetings/DOESNOTEXIST",
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: Join Room
# ---------------------------------------------------------------------------


class TestJoinRoomRoute:
    @pytest.mark.asyncio
    async def test_host_joins_own_pending_room(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        resp = await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={"listening_language": "en"},
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "joined"

    @pytest.mark.asyncio
    async def test_guest_without_name_is_rejected_or_sent_to_lobby(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        # Host activates the room first
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(token),
        )

        # Anonymous guest with no name
        resp = await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
        )

        # Should be 400 (MISSING_NAME) since no display_name
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_join_nonexistent_room_returns_404(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        resp = await client.post(
            "/api/v1/meetings/BADCODE/join",
            json={},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: Leave Room
# ---------------------------------------------------------------------------


class TestLeaveRoomRoute:
    @pytest.mark.asyncio
    async def test_host_leaves_room(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        # Host joins to activate
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(token),
        )

        resp = await client.post(
            f"/api/v1/meetings/{room_code}/leave",
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Test: End Room
# ---------------------------------------------------------------------------


class TestEndRoomRoute:
    @pytest.mark.asyncio
    async def test_host_ends_room(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        # Host joins to activate the room first
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(token),
        )

        resp = await client.post(
            f"/api/v1/meetings/{room_code}/end",
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "ended"

    @pytest.mark.asyncio
    async def test_non_host_cannot_end_room(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session, email="host@example.com")
        _seed_user(db_session, email="other@example.com")

        host_token = await _login(client, email="host@example.com")
        other_token = await _login(client, email="other@example.com")

        room_data = await _create_room_via_api(client, host_token)
        room_code = room_data["room_code"]

        resp = await client.post(
            f"/api/v1/meetings/{room_code}/end",
            headers=_auth_headers(other_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test: Update Config
# ---------------------------------------------------------------------------


class TestUpdateConfigRoute:
    @pytest.mark.asyncio
    async def test_host_updates_config(
        self,
        client: httpx.AsyncClient,
        db_session: Session,
        mock_connection_manager: AsyncMock,
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        # Activate room
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(token),
        )

        resp = await client.patch(
            f"/api/v1/meetings/{room_code}/config",
            json={"lock_room": True},
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["settings"]["lock_room"] is True
        mock_connection_manager.broadcast_to_room.assert_called_once_with(
            room_code,
            {"event": "room_config_updated", "settings": body["data"]["settings"]},
        )

    @pytest.mark.asyncio
    async def test_non_host_cannot_update_config(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session, email="host@example.com")
        _seed_user(db_session, email="other@example.com")

        host_token = await _login(client, email="host@example.com")
        other_token = await _login(client, email="other@example.com")

        room_data = await _create_room_via_api(client, host_token)
        room_code = room_data["room_code"]

        # Host joins to activate it
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(host_token),
        )

        resp = await client.patch(
            f"/api/v1/meetings/{room_code}/config",
            json={"lock_room": True},
            headers=_auth_headers(other_token),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_update_ended_or_pending_room(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        # Pending room
        resp = await client.patch(
            f"/api/v1/meetings/{room_code}/config",
            json={"lock_room": True},
            headers=_auth_headers(token),
        )
        assert resp.status_code == 400

        # Activate
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(token),
        )

        # End room
        await client.post(
            f"/api/v1/meetings/{room_code}/end",
            headers=_auth_headers(token),
        )

        # Ended room
        resp2 = await client.patch(
            f"/api/v1/meetings/{room_code}/config",
            json={"lock_room": True},
            headers=_auth_headers(token),
        )
        assert resp2.status_code == 400


# ---------------------------------------------------------------------------
# Test: Get Live State (Participants)
# ---------------------------------------------------------------------------


class TestGetLiveStateRoute:
    @pytest.mark.asyncio
    async def test_host_gets_live_state(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        resp = await client.get(
            f"/api/v1/meetings/{room_code}/participants",
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "active" in body["data"]
        assert "lobby" in body["data"]

    @pytest.mark.asyncio
    async def test_non_host_cannot_get_live_state(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session, email="host@example.com")
        _seed_user(db_session, email="other@example.com")

        host_token = await _login(client, email="host@example.com")
        other_token = await _login(client, email="other@example.com")

        room_data = await _create_room_via_api(client, host_token)
        room_code = room_data["room_code"]

        resp = await client.get(
            f"/api/v1/meetings/{room_code}/participants",
            headers=_auth_headers(other_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test: Meeting History
# ---------------------------------------------------------------------------


class TestMeetingHistoryRoute:
    @pytest.mark.asyncio
    async def test_returns_empty_history(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        resp = await client.get(
            "/api/v1/meetings/history",
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []

    @pytest.mark.asyncio
    async def test_history_after_ended_meeting(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        # Join to activate
        await client.post(
            f"/api/v1/meetings/{room_code}/join",
            json={},
            headers=_auth_headers(token),
        )
        # End the meeting
        await client.post(
            f"/api/v1/meetings/{room_code}/end",
            headers=_auth_headers(token),
        )

        resp = await client.get(
            "/api/v1/meetings/history",
            headers=_auth_headers(token),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] >= 1


# ---------------------------------------------------------------------------
# Test: Admit User
# ---------------------------------------------------------------------------


class TestAdmitUserRoute:
    @pytest.mark.asyncio
    async def test_admit_nonexistent_user_returns_400(
        self, client: httpx.AsyncClient, db_session: Session
    ) -> None:
        _seed_user(db_session)
        token = await _login(client)

        room_data = await _create_room_via_api(client, token)
        room_code = room_data["room_code"]

        resp = await client.post(
            f"/api/v1/meetings/{room_code}/admit/fake-user-id",
            headers=_auth_headers(token),
        )

        # User is not in the lobby, so should return 400
        assert resp.status_code == 400
