"""Tests for broker credential management API endpoints."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class TestBrokerStatus:
    def test_no_credentials_returns_empty(self, client: TestClient) -> None:
        # Ensure clean state
        client.delete("/api/broker/credentials")

        resp = client.get("/api/broker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_credentials"] is False
        assert data["broker"] == "oanda"
        assert data["account_id_masked"] == ""

    def test_after_save_returns_masked(self, client: TestClient) -> None:
        client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "001-004-1234567-001",
                "access_token": "test-token-abc",
                "server": "practice",
            },
        )
        resp = client.get("/api/broker/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_credentials"] is True
        assert data["account_id_masked"] == "***-001"
        assert data["server"] == "practice"


class TestSaveCredentials:
    def test_save_returns_masked_status(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "001-004-9999999-002",
                "access_token": "my-secret-token",
                "server": "practice",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_credentials"] is True
        assert data["account_id_masked"] == "***-002"
        assert data["server"] == "practice"

    def test_save_rejects_live_server(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "001-004-9999999-002",
                "access_token": "my-secret-token",
                "server": "live",
            },
        )
        assert resp.status_code == 422

    def test_token_never_in_response(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "account123",
                "access_token": "super-secret-token-xyz",
                "server": "practice",
            },
        )
        # Token must not appear anywhere in the response
        assert "super-secret-token-xyz" not in resp.text

        # Also check status endpoint
        resp2 = client.get("/api/broker/status")
        assert "super-secret-token-xyz" not in resp2.text

    def test_validation_rejects_empty_token(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "account1",
                "access_token": "",
                "server": "practice",
            },
        )
        assert resp.status_code == 422

    def test_validation_rejects_empty_account_id(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "",
                "access_token": "token1",
                "server": "practice",
            },
        )
        assert resp.status_code == 422

    def test_validation_rejects_uppercase_broker(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "OANDA",
                "account_id": "account1",
                "access_token": "token1",
                "server": "practice",
            },
        )
        assert resp.status_code == 422

    def test_validation_rejects_invalid_server(self, client: TestClient) -> None:
        resp = client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "account1",
                "access_token": "token1",
                "server": "invalid",
            },
        )
        assert resp.status_code == 422


class TestDeleteCredentials:
    def test_delete_removes(self, client: TestClient) -> None:
        client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "account1",
                "access_token": "token1",
                "server": "practice",
            },
        )
        resp = client.delete("/api/broker/credentials")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_credentials"] is False

    def test_delete_nonexistent_is_idempotent(self, client: TestClient) -> None:
        resp = client.delete("/api/broker/credentials")
        assert resp.status_code == 200
        assert resp.json()["has_credentials"] is False


class TestConnectionTest:
    def test_success_with_mock(self, client: TestClient) -> None:
        with patch(
            "aurex_trade.web.routers.broker.api.OANDAConnection"
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.connect.return_value = None
            mock_conn.disconnect.return_value = None

            resp = client.post(
                "/api/broker/test",
                json={
                    "broker": "oanda",
                    "use_stored": False,
                    "account_id": "account1",
                    "access_token": "token1",
                    "server": "practice",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert "Connected" in data["message"]

    def test_failure_returns_error_message(self, client: TestClient) -> None:
        with patch(
            "aurex_trade.web.routers.broker.api.OANDAConnection"
        ) as mock_conn_cls:
            from aurex_trade.adapters.oanda.connection import OANDAConnectionError

            mock_conn = mock_conn_cls.return_value
            mock_conn.connect.side_effect = OANDAConnectionError("Invalid credentials")

            resp = client.post(
                "/api/broker/test",
                json={
                    "broker": "oanda",
                    "use_stored": False,
                    "account_id": "account1",
                    "access_token": "bad-token",
                    "server": "practice",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is False
            assert "Invalid credentials" in data["message"]

    def test_use_stored_retrieves_from_store(self, client: TestClient) -> None:
        # First save credentials
        client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "stored-account",
                "access_token": "stored-token",
                "server": "practice",
            },
        )

        with patch(
            "aurex_trade.web.routers.broker.api.OANDAConnection"
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.connect.return_value = None
            mock_conn.disconnect.return_value = None

            resp = client.post(
                "/api/broker/test",
                json={"broker": "oanda", "use_stored": True},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    def test_use_stored_fails_when_none_saved(self, client: TestClient) -> None:
        # Ensure no credentials are stored
        client.delete("/api/broker/credentials")

        resp = client.post(
            "/api/broker/test",
            json={"broker": "oanda", "use_stored": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "No stored credentials" in data["message"]

    def test_provided_mode_requires_credentials(self, client: TestClient) -> None:
        resp = client.post(
            "/api/broker/test",
            json={
                "broker": "oanda",
                "use_stored": False,
                "account_id": "",
                "access_token": "",
                "server": "practice",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "required" in data["message"]


class TestAuthRequired:
    @pytest.fixture
    def unauthenticated_client(self) -> Generator[TestClient]:
        """Client without session cookie."""
        from aurex_trade.web.app import create_app

        app = create_app()
        with TestClient(app) as c:
            yield c

    def test_status_rejected(self, unauthenticated_client: TestClient) -> None:
        resp = unauthenticated_client.get("/api/broker/status")
        assert resp.status_code == 401

    def test_save_rejected(self, unauthenticated_client: TestClient) -> None:
        resp = unauthenticated_client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "x",
                "access_token": "y",
                "server": "practice",
            },
        )
        assert resp.status_code == 401
