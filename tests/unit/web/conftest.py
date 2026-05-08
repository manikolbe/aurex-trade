"""Shared fixtures for web tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aurex_trade.adapters.sqlite.session_store import SQLiteSessionStore
from aurex_trade.domain.models import User
from aurex_trade.web.app import create_app


def _create_authenticated_client() -> Generator[TestClient]:
    """Create a test client with a valid session cookie pre-installed."""
    app = create_app()
    with TestClient(app) as c:
        # Get the session store from the middleware
        session_store: SQLiteSessionStore | None = None
        for middleware in app.user_middleware:
            kwargs = middleware.kwargs
            if kwargs and "session_store" in kwargs:
                store = kwargs["session_store"]
                if isinstance(store, SQLiteSessionStore):
                    session_store = store
                break

        if session_store is None:
            raise RuntimeError("Could not find session store in middleware")

        # Create a test user and session
        user = User(id="test-user-id", email="test@gmail.com", name="Test User", avatar_url="")
        now = datetime.now(UTC)
        session_store.save_user(user, last_login=now)
        session_id = session_store.create_session(user.id, now + timedelta(hours=48))

        # Set session cookie on the client
        c.cookies.set("session_id", session_id)
        yield c


@pytest.fixture
def authenticated_client() -> Generator[TestClient]:
    """Create a test client with a valid session cookie pre-installed."""
    yield from _create_authenticated_client()


@pytest.fixture
def client() -> Generator[TestClient]:
    """Authenticated client — replaces local client fixtures in existing tests."""
    yield from _create_authenticated_client()
