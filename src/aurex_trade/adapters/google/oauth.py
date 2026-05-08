"""Google OAuth adapter — exchanges authorization codes for user info.

Uses authlib's OAuth2Session to handle the token exchange and userinfo fetch.
Implements OAuthProviderPort so future providers (Facebook, GitHub) can follow
the same interface.
"""

from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import OAuth2Client  # type: ignore[import-untyped]

from aurex_trade.domain.models import OAuthUserInfo

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_SCOPES = "openid email profile"


class GoogleOAuthAdapter:
    """OAuthProviderPort implementation for Google OAuth 2.0."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    @property
    def name(self) -> str:
        return "google"

    def get_authorization_url(self, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
        }
        return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthUserInfo:
        """Exchange authorization code for user info via token + userinfo endpoints."""
        client = OAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        token = client.fetch_token(
            _GOOGLE_TOKEN_URL,
            code=code,
            redirect_uri=self._redirect_uri,
        )
        access_token: str = token["access_token"]

        response = httpx.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("email_verified", False):
            msg = f"Email {data.get('email', '?')} is not verified by Google"
            raise ValueError(msg)

        return OAuthUserInfo(
            sub=data["sub"],
            email=data["email"],
            name=data.get("name", data["email"]),
            picture=data.get("picture", ""),
        )
