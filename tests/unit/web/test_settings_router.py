"""Tests for the settings API endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestSettingsEndpoint:
    """Tests for GET /api/settings."""

    def test_returns_200(self, client: TestClient) -> None:
        """Settings endpoint returns 200 OK."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_response_has_required_fields(self, client: TestClient) -> None:
        """Response contains all expected fields."""
        data = client.get("/api/settings").json()
        assert "trading_mode" in data
        assert "symbol" in data
        assert "interval_seconds" in data
        assert "log_level" in data

    def test_no_secrets_in_response(self, client: TestClient) -> None:
        """Response does not contain API keys or tokens."""
        data = client.get("/api/settings").json()
        keys = set(data.keys())
        # Ensure no secret-like fields are exposed
        secret_patterns = {"api_key", "token", "secret", "password", "credential"}
        for key in keys:
            assert not any(s in key.lower() for s in secret_patterns), (
                f"Potentially sensitive field exposed: {key}"
            )

    def test_trading_mode_is_string(self, client: TestClient) -> None:
        """Trading mode is returned as a string value."""
        data = client.get("/api/settings").json()
        assert isinstance(data["trading_mode"], str)
        assert len(data["trading_mode"]) > 0

    def test_interval_is_positive_int(self, client: TestClient) -> None:
        """Interval seconds is a positive integer."""
        data = client.get("/api/settings").json()
        assert isinstance(data["interval_seconds"], int)
        assert data["interval_seconds"] > 0
