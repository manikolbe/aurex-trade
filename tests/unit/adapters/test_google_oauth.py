"""Tests for the Google OAuth adapter."""

from unittest.mock import MagicMock, patch

from aurex_trade.adapters.google.oauth import GoogleOAuthAdapter


class TestGoogleOAuthAdapter:
    def _make_adapter(self) -> GoogleOAuthAdapter:
        return GoogleOAuthAdapter(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost:8000/auth/callback",
        )

    def test_name_property(self) -> None:
        adapter = self._make_adapter()
        assert adapter.name == "google"

    def test_get_authorization_url_includes_required_params(self) -> None:
        adapter = self._make_adapter()
        url = adapter.get_authorization_url(state="test-state-token")

        assert "accounts.google.com" in url
        assert "client_id=test-client-id" in url
        assert "state=test-state-token" in url
        assert "response_type=code" in url
        assert "scope=openid+email+profile" in url
        assert "redirect_uri=" in url

    @patch("aurex_trade.adapters.google.oauth.httpx.get")
    @patch("aurex_trade.adapters.google.oauth.OAuth2Client")
    def test_exchange_code_returns_user_info(
        self, mock_oauth_client_cls: MagicMock, mock_httpx_get: MagicMock
    ) -> None:
        # Mock token exchange
        mock_client = MagicMock()
        mock_oauth_client_cls.return_value = mock_client
        mock_client.fetch_token.return_value = {"access_token": "fake-access-token"}

        # Mock userinfo response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "sub": "google-123456",
            "email": "user@gmail.com",
            "name": "Test User",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_get.return_value = mock_response

        adapter = self._make_adapter()
        user_info = adapter.exchange_code("auth-code-from-google")

        assert user_info.sub == "google-123456"
        assert user_info.email == "user@gmail.com"
        assert user_info.name == "Test User"
        assert user_info.picture == "https://lh3.googleusercontent.com/photo.jpg"

        # Verify token exchange was called correctly
        mock_client.fetch_token.assert_called_once()

        # Verify userinfo was fetched with the access token
        mock_httpx_get.assert_called_once()
        call_kwargs = mock_httpx_get.call_args
        assert "Bearer fake-access-token" in str(call_kwargs)

    @patch("aurex_trade.adapters.google.oauth.httpx.get")
    @patch("aurex_trade.adapters.google.oauth.OAuth2Client")
    def test_exchange_code_handles_missing_optional_fields(
        self, mock_oauth_client_cls: MagicMock, mock_httpx_get: MagicMock
    ) -> None:
        mock_client = MagicMock()
        mock_oauth_client_cls.return_value = mock_client
        mock_client.fetch_token.return_value = {"access_token": "token"}

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "sub": "google-789",
            "email": "minimal@gmail.com",
            # No 'name' or 'picture' fields
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_get.return_value = mock_response

        adapter = self._make_adapter()
        user_info = adapter.exchange_code("code")

        assert user_info.name == "minimal@gmail.com"  # Falls back to email
        assert user_info.picture == ""
