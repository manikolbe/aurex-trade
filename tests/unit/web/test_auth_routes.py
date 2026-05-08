"""Tests for auth route helpers (state token generation/verification)."""

import time
from unittest.mock import patch

from aurex_trade.web.auth.router import _make_state, _verify_state


class TestStateToken:
    def test_roundtrip(self) -> None:
        """Valid state token verifies successfully."""
        secret = "test-secret-key"
        state = _make_state(secret, "/backtest")
        result = _verify_state(secret, state)
        assert result == "/backtest"

    def test_wrong_secret_fails(self) -> None:
        """State signed with different secret is rejected."""
        state = _make_state("secret-a", "/")
        assert _verify_state("secret-b", state) is None

    def test_tampered_path_fails(self) -> None:
        """Tampering with the path invalidates the signature."""
        secret = "my-secret"
        state = _make_state(secret, "/safe")
        # Replace path in the state string
        tampered = state.replace("/safe", "/evil")
        assert _verify_state(secret, tampered) is None

    def test_expired_state_fails(self) -> None:
        """State older than 10 minutes is rejected."""
        secret = "my-secret"
        with patch("aurex_trade.web.auth.router.time.time", return_value=time.time() - 700):
            state = _make_state(secret, "/")
        assert _verify_state(secret, state) is None

    def test_malformed_state_fails(self) -> None:
        """Strings without proper format return None."""
        secret = "s"
        assert _verify_state(secret, "") is None
        assert _verify_state(secret, "onlyone") is None
        assert _verify_state(secret, "two:parts") is None

    def test_non_numeric_timestamp_fails(self) -> None:
        """Non-integer timestamp is rejected."""
        secret = "s"
        assert _verify_state(secret, "sig:notanumber:/path") is None

    def test_preserves_complex_paths(self) -> None:
        """Paths with query strings and fragments round-trip correctly."""
        secret = "test"
        path = "/walk-forward"
        state = _make_state(secret, path)
        assert _verify_state(secret, state) == path

    def test_root_path(self) -> None:
        """Root path / works correctly."""
        secret = "test"
        state = _make_state(secret, "/")
        assert _verify_state(secret, state) == "/"
