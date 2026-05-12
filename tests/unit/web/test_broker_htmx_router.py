"""Tests for broker HTMX endpoints returning HTML fragments."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestHtmxBrokerStatus:
    """GET /htmx/broker/status returns broker form HTML partial."""

    def test_no_credentials_shows_not_configured(self, client: TestClient) -> None:
        client.delete("/api/broker/credentials")
        resp = client.get("/htmx/broker/status")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Not configured" in resp.text

    def test_with_credentials_shows_configured(self, client: TestClient) -> None:
        client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "001-004-1234567-001",
                "access_token": "test-token",
                "server": "practice",
            },
        )
        resp = client.get("/htmx/broker/status")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Configured" in resp.text
        assert "***-001" in resp.text

    def test_token_never_in_html(self, client: TestClient) -> None:
        client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "account1",
                "access_token": "super-secret-token",
                "server": "practice",
            },
        )
        resp = client.get("/htmx/broker/status")
        assert "super-secret-token" not in resp.text


class TestHtmxSaveCredentials:
    """PUT /htmx/broker/credentials accepts form data, returns HTML."""

    def test_save_returns_html_with_success(self, client: TestClient) -> None:
        resp = client.put(
            "/htmx/broker/credentials",
            data={
                "broker": "oanda",
                "account_id": "001-004-9999999-002",
                "access_token": "my-secret-token",
                "server": "practice",
            },
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Credentials saved successfully" in resp.text

    def test_save_shows_masked_account(self, client: TestClient) -> None:
        resp = client.put(
            "/htmx/broker/credentials",
            data={
                "broker": "oanda",
                "account_id": "001-004-1234567-003",
                "access_token": "token",
                "server": "practice",
            },
        )
        assert "***-003" in resp.text

    def test_rejects_live_server(self, client: TestClient) -> None:
        resp = client.put(
            "/htmx/broker/credentials",
            data={
                "broker": "oanda",
                "account_id": "account1",
                "access_token": "token1",
                "server": "live",
            },
        )
        assert resp.status_code == 422

    def test_rejects_unsupported_broker(self, client: TestClient) -> None:
        resp = client.put(
            "/htmx/broker/credentials",
            data={
                "broker": "binance",
                "account_id": "account1",
                "access_token": "token1",
                "server": "practice",
            },
        )
        assert resp.status_code == 422


class TestHtmxDeleteCredentials:
    """DELETE /htmx/broker/credentials returns empty form HTML."""

    def test_delete_returns_not_configured(self, client: TestClient) -> None:
        # First save something
        client.put(
            "/api/broker/credentials",
            json={
                "broker": "oanda",
                "account_id": "account1",
                "access_token": "token1",
                "server": "practice",
            },
        )
        resp = client.delete("/htmx/broker/credentials")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Not configured" in resp.text

    def test_delete_nonexistent_is_idempotent(self, client: TestClient) -> None:
        client.delete("/api/broker/credentials")
        resp = client.delete("/htmx/broker/credentials")
        assert resp.status_code == 200
        assert "Not configured" in resp.text


class TestHtmxTestConnection:
    """POST /htmx/broker/test accepts form data, returns status HTML."""

    def test_success_returns_success_html(self, client: TestClient) -> None:
        with patch(
            "aurex_trade.web.routers.broker.htmx.OANDAConnection"
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.connect.return_value = None
            mock_conn.disconnect.return_value = None

            resp = client.post(
                "/htmx/broker/test",
                data={
                    "broker": "oanda",
                    "use_stored": "false",
                    "account_id": "account1",
                    "access_token": "token1",
                    "server": "practice",
                },
            )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "alert-success" in resp.text
        assert "Connected successfully" in resp.text

    def test_failure_returns_error_html(self, client: TestClient) -> None:
        with patch(
            "aurex_trade.web.routers.broker.htmx.OANDAConnection"
        ) as mock_conn_cls:
            from aurex_trade.adapters.oanda.connection import OANDAConnectionError

            mock_conn = mock_conn_cls.return_value
            mock_conn.connect.side_effect = OANDAConnectionError("Invalid credentials")

            resp = client.post(
                "/htmx/broker/test",
                data={
                    "broker": "oanda",
                    "use_stored": "false",
                    "account_id": "account1",
                    "access_token": "bad-token",
                    "server": "practice",
                },
            )
        assert resp.status_code == 200
        assert "alert-error" in resp.text
        assert "Invalid credentials" in resp.text

    def test_use_stored_true_string_coercion(self, client: TestClient) -> None:
        """HTMX sends booleans as strings — 'true' must coerce to True."""
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
            "aurex_trade.web.routers.broker.htmx.OANDAConnection"
        ) as mock_conn_cls:
            mock_conn = mock_conn_cls.return_value
            mock_conn.connect.return_value = None
            mock_conn.disconnect.return_value = None

            resp = client.post(
                "/htmx/broker/test",
                data={"broker": "oanda", "use_stored": "true"},
            )
        assert resp.status_code == 200
        assert "alert-success" in resp.text

    def test_use_stored_no_credentials_returns_error(self, client: TestClient) -> None:
        client.delete("/api/broker/credentials")
        resp = client.post(
            "/htmx/broker/test",
            data={"broker": "oanda", "use_stored": "true"},
        )
        assert resp.status_code == 200
        assert "alert-error" in resp.text
        assert "No stored credentials" in resp.text

    def test_provided_mode_missing_creds_returns_error(self, client: TestClient) -> None:
        resp = client.post(
            "/htmx/broker/test",
            data={
                "broker": "oanda",
                "use_stored": "false",
                "account_id": "",
                "access_token": "",
                "server": "practice",
            },
        )
        assert resp.status_code == 200
        assert "alert-error" in resp.text
        assert "required" in resp.text
