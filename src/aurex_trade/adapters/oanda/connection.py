"""OANDA API connection — wraps httpx.Client for the v20 REST API."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from aurex_trade.config import OANDAConfig

_BASE_URLS: dict[str, str] = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}

log = structlog.get_logger()


class OANDAConnectionError(Exception):
    """Raised when the OANDA connection cannot be established."""


class OANDAAPIError(Exception):
    """Raised when the OANDA API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"OANDA API error {status_code}: {message}")


class OANDAConnection:
    """Manages HTTP communication with the OANDA v20 REST API.

    Wraps httpx.Client with authentication, base URL selection,
    and error handling. All adapter classes share a single connection.
    """

    def __init__(self, config: OANDAConfig) -> None:
        self._config = config
        self._client: httpx.Client | None = None

        base_url = _BASE_URLS.get(config.server)
        if base_url is None:
            msg = f"Invalid OANDA server: {config.server!r} (expected 'practice' or 'live')"
            raise OANDAConnectionError(msg)
        self._base_url = base_url

    def connect(self) -> None:
        """Create HTTP client and validate credentials against the OANDA API."""
        # OANDA tokens are ASCII-only (alphanumeric + hyphens).
        # Strip non-ASCII chars that creep in from copy-paste (e.g. Cyrillic homoglyphs).
        token = self._config.access_token.strip()
        token = token.encode("ascii", errors="ignore").decode("ascii")

        if not token:
            raise OANDAConnectionError(
                "OANDA access token is empty after removing invalid characters."
            )

        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

        # Validate credentials by fetching account summary
        try:
            self.get(f"/v3/accounts/{self._config.account_id}")
        except OANDAAPIError as exc:
            self.disconnect()
            raise OANDAConnectionError(f"Failed to validate OANDA credentials: {exc}") from exc
        except httpx.HTTPError as exc:
            self.disconnect()
            raise OANDAConnectionError(f"Network error connecting to OANDA: {exc}") from exc

        log.info(
            "oanda_connected",
            server=self._config.server,
            account_id=self._config.account_id,
        )

    def disconnect(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
            log.info("oanda_disconnected")

    @property
    def is_connected(self) -> bool:
        """Return True if the HTTP client is active."""
        return self._client is not None

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Send a GET request to the OANDA API."""
        client = self._require_client()
        response = client.get(path, params=params)
        return self._handle_response(response)

    def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        """Send a POST request to the OANDA API."""
        client = self._require_client()
        response = client.post(path, json=json)
        return self._handle_response(response)

    def _require_client(self) -> httpx.Client:
        """Return the active client or raise if not connected."""
        if self._client is None:
            raise OANDAConnectionError("Not connected — call connect() first")
        return self._client

    @staticmethod
    def _handle_response(response: httpx.Response) -> dict[str, Any]:
        """Parse response JSON, raising OANDAAPIError on non-2xx."""
        if response.status_code >= 400:
            try:
                body = response.json()
                message = body.get("errorMessage", response.text)
            except Exception:
                message = response.text
            raise OANDAAPIError(response.status_code, message)

        result: dict[str, Any] = response.json()
        return result
