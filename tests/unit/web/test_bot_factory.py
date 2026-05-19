"""Tests for create_bot_engine — bot factory function."""

from unittest.mock import MagicMock, patch

import pytest

from aurex_trade.ports.credential_store import BrokerCredentialInfo, BrokerCredentials
from aurex_trade.web._bot_factory import create_bot_engine


class FakeCredentialStore:
    """Minimal CredentialStorePort implementation for testing."""

    def __init__(self, creds: BrokerCredentials | None = None) -> None:
        self._creds = creds

    def retrieve(self, user_id: str, broker: str) -> BrokerCredentials | None:
        return self._creds

    def store(
        self,
        user_id: str,
        broker: str,
        account_id: str,
        access_token: str,
        server: str,
    ) -> None:
        pass

    def delete(self, user_id: str, broker: str) -> None:
        pass

    def has_credentials(self, user_id: str, broker: str) -> bool:
        return self._creds is not None

    def get_masked_info(
        self, user_id: str, broker: str
    ) -> BrokerCredentialInfo | None:
        return None


def _practice_creds() -> BrokerCredentials:
    return BrokerCredentials(
        account_id="001-001-0000001-001",
        access_token="fake-token-abc",
        server="practice",
    )


def _live_creds() -> BrokerCredentials:
    return BrokerCredentials(
        account_id="001-001-0000001-001",
        access_token="fake-token-abc",
        server="live",
    )


def _valid_strategy_params() -> dict[str, int | float]:
    return {"short_window": 10, "long_window": 30}


def _valid_risk_params() -> dict[str, int | float | bool]:
    return {
        "max_position_size": 5,
        "max_daily_loss": 200.0,
        "max_trades_per_day": 10,
    }


class TestHappyPath:
    """create_bot_engine with valid practice credentials."""

    @patch("aurex_trade.web._bot_factory.SQLiteRepository")
    @patch("aurex_trade.web._bot_factory.OANDAConnection")
    def test_returns_engine_and_connection(
        self, mock_conn_cls: MagicMock, mock_repo_cls: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_conn_cls.return_value = mock_conn
        store = FakeCredentialStore(_practice_creds())

        engine, connection = create_bot_engine(
            user_id="user-1",
            strategy_name="sma_crossover",
            strategy_params=_valid_strategy_params(),
            risk_params=_valid_risk_params(),
            symbol="XAU_USD",
            interval_seconds=60,
            credential_store=store,
        )

        assert engine is not None
        assert connection is mock_conn
        mock_conn.connect.assert_called_once()

    @patch("aurex_trade.web._bot_factory.SQLiteRepository")
    @patch("aurex_trade.web._bot_factory.OANDAConnection")
    def test_engine_configured_with_correct_params(
        self, mock_conn_cls: MagicMock, mock_repo_cls: MagicMock
    ) -> None:
        mock_conn_cls.return_value = MagicMock()
        store = FakeCredentialStore(_practice_creds())

        engine, _ = create_bot_engine(
            user_id="user-1",
            strategy_name="sma_crossover",
            strategy_params=_valid_strategy_params(),
            risk_params=_valid_risk_params(),
            symbol="EUR_USD",
            interval_seconds=30,
            credential_store=store,
        )

        assert engine._symbol == "EUR_USD"
        assert engine._interval_seconds == 30
        assert engine._user_id == "user-1"

    @patch("aurex_trade.web._bot_factory.SQLiteRepository")
    @patch("aurex_trade.web._bot_factory.OANDAConnection")
    def test_works_with_rsi_strategy(
        self, mock_conn_cls: MagicMock, mock_repo_cls: MagicMock
    ) -> None:
        mock_conn_cls.return_value = MagicMock()
        store = FakeCredentialStore(_practice_creds())

        engine, _ = create_bot_engine(
            user_id="user-1",
            strategy_name="rsi_mean_reversion",
            strategy_params={"period": 14, "overbought": 70, "oversold": 30},
            risk_params=_valid_risk_params(),
            symbol="XAU_USD",
            interval_seconds=60,
            credential_store=store,
        )

        assert engine._strategy.name == "rsi_mean_reversion"


class TestMissingCredentials:
    """create_bot_engine when credential store returns None."""

    def test_raises_value_error(self) -> None:
        store = FakeCredentialStore(None)

        with pytest.raises(ValueError, match="No OANDA credentials found"):
            create_bot_engine(
                user_id="user-1",
                strategy_name="sma_crossover",
                strategy_params=_valid_strategy_params(),
                risk_params=_valid_risk_params(),
                symbol="XAU_USD",
                interval_seconds=60,
                credential_store=store,
            )

    def test_error_includes_user_id(self) -> None:
        store = FakeCredentialStore(None)

        with pytest.raises(ValueError, match="user-42"):
            create_bot_engine(
                user_id="user-42",
                strategy_name="sma_crossover",
                strategy_params=_valid_strategy_params(),
                risk_params=_valid_risk_params(),
                symbol="XAU_USD",
                interval_seconds=60,
                credential_store=store,
            )


class TestLiveServerRejected:
    """create_bot_engine rejects live server credentials."""

    def test_raises_value_error_for_live(self) -> None:
        store = FakeCredentialStore(_live_creds())

        with pytest.raises(ValueError, match="Live trading is not permitted"):
            create_bot_engine(
                user_id="user-1",
                strategy_name="sma_crossover",
                strategy_params=_valid_strategy_params(),
                risk_params=_valid_risk_params(),
                symbol="XAU_USD",
                interval_seconds=60,
                credential_store=store,
            )

    def test_error_mentions_server_value(self) -> None:
        store = FakeCredentialStore(_live_creds())

        with pytest.raises(ValueError, match="server='live'"):
            create_bot_engine(
                user_id="user-1",
                strategy_name="sma_crossover",
                strategy_params=_valid_strategy_params(),
                risk_params=_valid_risk_params(),
                symbol="XAU_USD",
                interval_seconds=60,
                credential_store=store,
            )


class TestUnknownStrategy:
    """create_bot_engine with an unregistered strategy name."""

    def test_raises_value_error(self) -> None:
        store = FakeCredentialStore(_practice_creds())

        with pytest.raises(ValueError, match="Unknown strategy"):
            create_bot_engine(
                user_id="user-1",
                strategy_name="nonexistent_strategy",
                strategy_params={},
                risk_params=_valid_risk_params(),
                symbol="XAU_USD",
                interval_seconds=60,
                credential_store=store,
            )

    def test_does_not_connect_on_unknown_strategy(self) -> None:
        """Strategy validation happens before network I/O."""
        store = FakeCredentialStore(_practice_creds())

        with patch("aurex_trade.web._bot_factory.OANDAConnection") as mock_cls:
            with pytest.raises(ValueError):
                create_bot_engine(
                    user_id="user-1",
                    strategy_name="nonexistent_strategy",
                    strategy_params={},
                    risk_params=_valid_risk_params(),
                    symbol="XAU_USD",
                    interval_seconds=60,
                    credential_store=store,
                )

            mock_cls.assert_not_called()

    def test_error_lists_available_strategies(self) -> None:
        store = FakeCredentialStore(_practice_creds())

        with pytest.raises(ValueError, match="sma_crossover"):
            create_bot_engine(
                user_id="user-1",
                strategy_name="bad",
                strategy_params={},
                risk_params=_valid_risk_params(),
                symbol="XAU_USD",
                interval_seconds=60,
                credential_store=store,
            )
