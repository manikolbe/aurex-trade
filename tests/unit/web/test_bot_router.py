"""Tests for the bot control API endpoints."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from aurex_trade.web.app import create_app


@pytest.fixture
def client() -> Generator[TestClient]:
    """Create test client with lifespan."""
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestBotStatus:
    """Tests for GET /api/bot/status."""

    def test_returns_200(self, client: TestClient) -> None:
        """Status endpoint returns 200."""
        resp = client.get("/api/bot/status")
        assert resp.status_code == 200

    def test_not_running_by_default(self, client: TestClient) -> None:
        """Bot is not running when no task has been started."""
        data = client.get("/api/bot/status").json()
        assert data["running"] is False
        assert data["task_id"] is None


class TestBotStart:
    """Tests for POST /api/bot/start."""

    def test_returns_200(self, client: TestClient) -> None:
        """Start endpoint returns 200."""
        resp = client.post("/api/bot/start")
        assert resp.status_code == 200

    def test_not_implemented_returns_not_running(self, client: TestClient) -> None:
        """Start currently returns not-running (scaffold)."""
        data = client.post("/api/bot/start").json()
        assert data["running"] is False


class TestBotStop:
    """Tests for POST /api/bot/stop."""

    def test_returns_200(self, client: TestClient) -> None:
        """Stop endpoint returns 200."""
        resp = client.post("/api/bot/stop")
        assert resp.status_code == 200

    def test_stop_when_not_running(self, client: TestClient) -> None:
        """Stop when bot isn't running returns not-running status."""
        data = client.post("/api/bot/stop").json()
        assert data["running"] is False
        assert data["task_id"] is None

    def test_stop_clears_task_id(self, client: TestClient) -> None:
        """Stop clears the bot_task_id from app state."""
        from uuid import uuid4

        # Simulate a running bot by setting the state directly
        client.app.state.bot_task_id = uuid4()  # type: ignore[union-attr]
        resp = client.post("/api/bot/stop")
        assert resp.json()["running"] is False
        assert client.app.state.bot_task_id is None  # type: ignore[union-attr]
