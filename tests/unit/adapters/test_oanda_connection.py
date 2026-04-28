"""Tests for the OANDA connection wrapper."""

from unittest.mock import MagicMock, patch

import httpx

from aurex_trade.adapters.oanda.connection import (
    OANDAAPIError,
    OANDAConnection,
    OANDAConnectionError,
)
from aurex_trade.config import OANDAConfig


def _make_config(
    token: str = "test-token",  # noqa: S107
    account_id: str = "101-001-123",
    server: str = "practice",
) -> OANDAConfig:
    return OANDAConfig(access_token=token, account_id=account_id, server=server)


class TestConnect:
    def test_connect_success(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)

        with patch.object(OANDAConnection, "get", return_value={"account": {}}):
            conn.connect()

        assert conn.is_connected is True
        conn.disconnect()

    def test_connect_invalid_credentials(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)

        with patch.object(
            OANDAConnection,
            "get",
            side_effect=OANDAAPIError(401, "Invalid access token"),
        ):
            try:
                conn.connect()
                assert False, "Should have raised"  # noqa: B011
            except OANDAConnectionError:
                pass

        assert conn.is_connected is False

    def test_connect_network_error(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)

        with patch.object(
            OANDAConnection,
            "get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            try:
                conn.connect()
                assert False, "Should have raised"  # noqa: B011
            except OANDAConnectionError:
                pass

        assert conn.is_connected is False

    def test_invalid_server_raises(self) -> None:
        config = _make_config(server="invalid")
        try:
            OANDAConnection(config)
            assert False, "Should have raised"  # noqa: B011
        except OANDAConnectionError:
            pass


class TestDisconnect:
    def test_disconnect_closes_client(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)

        with patch.object(OANDAConnection, "get", return_value={"account": {}}):
            conn.connect()

        assert conn.is_connected is True
        conn.disconnect()
        assert conn.is_connected is False

    def test_disconnect_when_not_connected(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)
        conn.disconnect()  # Should not raise


class TestRequireClient:
    def test_get_raises_when_not_connected(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)
        try:
            conn.get("/v3/accounts/123")
            assert False, "Should have raised"  # noqa: B011
        except OANDAConnectionError:
            pass

    def test_post_raises_when_not_connected(self) -> None:
        config = _make_config()
        conn = OANDAConnection(config)
        try:
            conn.post("/v3/accounts/123/orders", json={})
            assert False, "Should have raised"  # noqa: B011
        except OANDAConnectionError:
            pass


class TestHandleResponse:
    def test_success_returns_json(self) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.json.return_value = {"key": "value"}

        result = OANDAConnection._handle_response(response)
        assert result == {"key": "value"}

    def test_error_raises_api_error(self) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.json.return_value = {"errorMessage": "Bad request"}
        response.text = "Bad request"

        try:
            OANDAConnection._handle_response(response)
            assert False, "Should have raised"  # noqa: B011
        except OANDAAPIError as exc:
            assert exc.status_code == 400
            assert "Bad request" in str(exc)

    def test_error_with_non_json_body(self) -> None:
        response = MagicMock(spec=httpx.Response)
        response.status_code = 500
        response.json.side_effect = ValueError("Not JSON")
        response.text = "Internal Server Error"

        try:
            OANDAConnection._handle_response(response)
            assert False, "Should have raised"  # noqa: B011
        except OANDAAPIError as exc:
            assert exc.status_code == 500
            assert "Internal Server Error" in str(exc)


class TestBaseURL:
    def test_practice_url(self) -> None:
        config = _make_config(server="practice")
        conn = OANDAConnection(config)
        assert "fxpractice" in conn._base_url

    def test_live_url(self) -> None:
        config = _make_config(server="live")
        conn = OANDAConnection(config)
        assert "fxtrade" in conn._base_url
