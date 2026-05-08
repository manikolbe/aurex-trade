"""Tests for the authentication middleware."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from aurex_trade.adapters.sqlite.session_store import SQLiteSessionStore
from aurex_trade.domain.models import User
from aurex_trade.web.auth.middleware import AuthMiddleware


def _create_app(session_store: SQLiteSessionStore) -> Starlette:
    """Create a minimal test app with auth middleware."""

    async def protected_page(request: Request) -> PlainTextResponse:
        user: User = request.state.user
        return PlainTextResponse(f"Hello {user.name}")

    async def api_endpoint(request: Request) -> PlainTextResponse:
        return PlainTextResponse("api ok")

    async def htmx_endpoint(request: Request) -> PlainTextResponse:
        return PlainTextResponse("htmx ok")

    async def public_health(request: Request) -> PlainTextResponse:
        return PlainTextResponse("healthy")

    async def auth_login(request: Request) -> PlainTextResponse:
        return PlainTextResponse("login page")

    app = Starlette(
        routes=[
            Route("/", protected_page),
            Route("/backtest", protected_page),
            Route("/api/data", api_endpoint),
            Route("/htmx/poll", htmx_endpoint),
            Route("/api/health", public_health),
            Route("/auth/login", auth_login),
        ],
    )
    app.add_middleware(AuthMiddleware, session_store=session_store, expiry_hours=48)
    return app


def _setup(tmp_path: Path) -> tuple[TestClient, SQLiteSessionStore, str]:
    """Set up test client with a valid session."""
    store = SQLiteSessionStore(db_path=tmp_path / "test.db")
    user = User(id="user-123", email="test@gmail.com", name="Test User", avatar_url="")
    now = datetime.now(UTC)
    store.save_user(user, last_login=now)
    session_id = store.create_session(user.id, now + timedelta(hours=48))

    app = _create_app(store)
    client = TestClient(app, follow_redirects=False)
    return client, store, session_id


class TestPublicPaths:
    def test_health_endpoint_accessible_without_auth(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        app = _create_app(store)
        client = TestClient(app)

        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.text == "healthy"

    def test_auth_login_accessible_without_auth(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        app = _create_app(store)
        client = TestClient(app)

        response = client.get("/auth/login")
        assert response.status_code == 200
        assert response.text == "login page"


class TestUnauthenticatedRequests:
    def test_page_request_redirects_to_login(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        app = _create_app(store)
        client = TestClient(app, follow_redirects=False)

        response = client.get("/")
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    def test_api_request_returns_401_json(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        app = _create_app(store)
        client = TestClient(app)

        response = client.get("/api/data")
        assert response.status_code == 401
        assert response.json()["error"] == "Not authenticated"

    def test_htmx_request_returns_401_with_redirect_header(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        app = _create_app(store)
        client = TestClient(app)

        response = client.get("/htmx/poll", headers={"HX-Request": "true"})
        assert response.status_code == 401
        assert response.headers["HX-Redirect"] == "/auth/login"


class TestAuthenticatedRequests:
    def test_valid_session_allows_access(self, tmp_path: Path) -> None:
        client, _, session_id = _setup(tmp_path)
        response = client.get("/", cookies={"session_id": session_id})
        assert response.status_code == 200
        assert "Hello Test User" in response.text

    def test_expired_session_redirects(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        user = User(id="user-123", email="test@gmail.com", name="Test", avatar_url="")
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)
        # Create already-expired session
        session_id = store.create_session(user.id, now - timedelta(hours=1))

        app = _create_app(store)
        client = TestClient(app, follow_redirects=False)

        response = client.get("/", cookies={"session_id": session_id})
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    def test_invalid_session_id_redirects(self, tmp_path: Path) -> None:
        store = SQLiteSessionStore(db_path=tmp_path / "test.db")
        app = _create_app(store)
        client = TestClient(app, follow_redirects=False)

        response = client.get("/", cookies={"session_id": "bogus-session"})
        assert response.status_code == 302

    def test_sliding_expiry_extends_session(self, tmp_path: Path) -> None:
        client, store, session_id = _setup(tmp_path)

        # Access the page
        client.get("/", cookies={"session_id": session_id})

        # Session expiry should have been extended
        session = store.get_session(session_id)
        assert session is not None
        # New expiry should be ~48h from now (not from original creation)
        expected_min = datetime.now(UTC) + timedelta(hours=47)
        assert session.expires_at > expected_min
