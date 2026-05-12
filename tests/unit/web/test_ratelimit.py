"""Tests for API rate limiting."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from aurex_trade.web.ratelimit import RateLimitConfig, get_client_ip


def _unique_ip() -> str:
    """Generate a unique IP per test to avoid cross-test contamination."""
    return f"10.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}"


class TestRateLimitConfig:
    """Test rate limit configuration."""

    def test_defaults_match_issue_spec(self) -> None:
        config = RateLimitConfig()
        assert config.enabled is True
        assert config.storage_uri == "memory://"
        assert config.default == "60/minute"
        assert config.compute == "5/minute"
        assert config.bot_control == "3/minute"
        assert config.read == "120/minute"
        assert config.auth == "10/minute"
        assert config.auth_logout == "5/minute"

    def test_env_override_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATELIMIT_COMPUTE", "10/minute")
        monkeypatch.setenv("RATELIMIT_ENABLED", "false")
        config = RateLimitConfig()
        assert config.compute == "10/minute"
        assert config.enabled is False


class TestGetClientIp:
    """Test client IP extraction."""

    def test_extracts_from_x_forwarded_for(self) -> None:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "203.0.113.50, 70.41.3.18"}
        assert get_client_ip(request) == "203.0.113.50"

    def test_single_ip_in_forwarded_for(self) -> None:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"X-Forwarded-For": "192.168.1.1"}
        assert get_client_ip(request) == "192.168.1.1"

    def test_falls_back_to_client_host(self) -> None:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {}
        request.client.host = "10.0.0.5"
        assert get_client_ip(request) == "10.0.0.5"

    def test_returns_default_when_no_client(self) -> None:
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {}
        request.client = None
        assert get_client_ip(request) == "127.0.0.1"


class TestRateLimitEndpoints:
    """Integration tests for rate limiting on endpoints."""

    @pytest.fixture
    def client(self, authenticated_client: TestClient) -> TestClient:
        return authenticated_client

    def test_compute_endpoint_returns_429_after_limit(self, client: TestClient) -> None:
        ip = _unique_ip()
        headers = {"X-Forwarded-For": ip}
        backtest_payload = {
            "strategy": "sma_crossover",
            "symbol": "XAU_USD",
            "granularity": "M1",
            "params": {"short_window": 10, "long_window": 30},
            "capital": 10000,
            "risk_per_trade": 0.02,
        }

        # Send requests up to the limit (5/minute)
        for _ in range(5):
            resp = client.post("/api/backtest", json=backtest_payload, headers=headers)
            assert resp.status_code in (202, 422), f"Unexpected: {resp.status_code}"

        # 6th request should be rate limited
        resp = client.post("/api/backtest", json=backtest_payload, headers=headers)
        assert resp.status_code == 429

    def test_bot_control_limited_at_3(self, client: TestClient) -> None:
        ip = _unique_ip()
        headers = {"X-Forwarded-For": ip}

        for _ in range(3):
            resp = client.post("/api/bot/start", headers=headers)
            assert resp.status_code != 429

        resp = client.post("/api/bot/start", headers=headers)
        assert resp.status_code == 429

    def test_health_endpoint_exempt(self, client: TestClient) -> None:
        ip = _unique_ip()
        headers = {"X-Forwarded-For": ip}

        # Health should never be rate limited even with many requests
        for _ in range(100):
            resp = client.get("/api/health", headers=headers)
            assert resp.status_code == 200

    def test_different_ips_have_independent_limits(self, client: TestClient) -> None:
        ip1 = _unique_ip()
        ip2 = _unique_ip()

        # Exhaust limit for ip1
        for _ in range(3):
            client.post("/api/bot/start", headers={"X-Forwarded-For": ip1})

        # ip1 is limited
        resp = client.post("/api/bot/start", headers={"X-Forwarded-For": ip1})
        assert resp.status_code == 429

        # ip2 still has budget
        resp = client.post("/api/bot/start", headers={"X-Forwarded-For": ip2})
        assert resp.status_code != 429


class TestRateLimitResponse:
    """Test 429 response format."""

    @pytest.fixture
    def client(self, authenticated_client: TestClient) -> TestClient:
        return authenticated_client

    def test_429_json_matches_error_schema(self, client: TestClient) -> None:
        ip = _unique_ip()
        headers = {"X-Forwarded-For": ip}

        # Exhaust bot control limit
        for _ in range(3):
            client.post("/api/bot/start", headers=headers)

        resp = client.post("/api/bot/start", headers=headers)
        assert resp.status_code == 429
        data = resp.json()
        assert data["error"] == "Rate limit exceeded"
        assert "detail" in data
        assert data["status_code"] == 429

    def test_429_includes_retry_after_header(self, client: TestClient) -> None:
        ip = _unique_ip()
        headers = {"X-Forwarded-For": ip}

        # Exhaust bot control limit
        for _ in range(3):
            client.post("/api/bot/start", headers=headers)

        resp = client.post("/api/bot/start", headers=headers)
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        retry_after = int(resp.headers["Retry-After"])
        assert retry_after > 0

    def test_retry_after_value_is_positive_integer(self, client: TestClient) -> None:
        ip = _unique_ip()
        headers = {"X-Forwarded-For": ip}

        for _ in range(3):
            client.post("/api/bot/stop", headers=headers)

        resp = client.post("/api/bot/stop", headers=headers)
        assert resp.status_code == 429
        retry_after = resp.headers.get("Retry-After", "")
        assert retry_after.isdigit()
        assert int(retry_after) > 0
