"""Tests for the health check endpoint."""

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


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    def test_returns_200(self, client: TestClient) -> None:
        """Health endpoint returns 200 OK."""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_response_shape(self, client: TestClient) -> None:
        """Response contains status, timestamp, and version."""
        data = client.get("/api/health").json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "version" in data

    def test_version_is_string(self, client: TestClient) -> None:
        """Version field is a non-empty string."""
        data = client.get("/api/health").json()
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_timestamp_is_iso_format(self, client: TestClient) -> None:
        """Timestamp is a valid ISO 8601 datetime string."""
        from datetime import datetime

        data = client.get("/api/health").json()
        # Should not raise
        dt = datetime.fromisoformat(data["timestamp"])
        assert dt is not None
