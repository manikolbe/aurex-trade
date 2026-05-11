"""Tests for UserDefaultsStore — per-user strategy and risk/cost defaults."""

from pathlib import Path

from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore


class TestStrategyDefaults:
    def test_save_and_get_strategy_defaults(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        params = {"short_window": 15, "long_window": 40}
        store.save_strategy_defaults("user1", "sma_crossover", params)

        result = store.get_strategy_defaults("user1", "sma_crossover")
        assert result == {"short_window": 15, "long_window": 40}
        store.close()

    def test_upsert_overwrites_existing(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        params1 = {"short_window": 10, "long_window": 30}
        params2 = {"short_window": 20, "long_window": 50}
        store.save_strategy_defaults("user1", "sma_crossover", params1)
        store.save_strategy_defaults("user1", "sma_crossover", params2)

        result = store.get_strategy_defaults("user1", "sma_crossover")
        assert result == {"short_window": 20, "long_window": 50}
        store.close()

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        result = store.get_strategy_defaults("user1", "sma_crossover")
        assert result is None
        store.close()

    def test_preferred_strategy_set_and_get(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_strategy_defaults(
            "user1", "sma_crossover", {"short_window": 10}, is_preferred=True
        )

        assert store.get_preferred_strategy("user1") == "sma_crossover"
        store.close()

    def test_preferred_strategy_clears_old(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_strategy_defaults(
            "user1", "sma_crossover", {"short_window": 10}, is_preferred=True
        )
        store.save_strategy_defaults(
            "user1", "rsi_mean_reversion", {"period": 14}, is_preferred=True
        )

        assert store.get_preferred_strategy("user1") == "rsi_mean_reversion"
        store.close()

    def test_get_all_strategy_defaults(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_strategy_defaults("user1", "sma_crossover", {"short_window": 15})
        store.save_strategy_defaults("user1", "rsi_mean_reversion", {"period": 21})

        result = store.get_all_strategy_defaults("user1")
        assert result == {
            "sma_crossover": {"short_window": 15},
            "rsi_mean_reversion": {"period": 21},
        }
        store.close()

    def test_strategy_defaults_scoped_by_user(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_strategy_defaults("user1", "sma_crossover", {"short_window": 10})
        store.save_strategy_defaults("user2", "sma_crossover", {"short_window": 20})

        assert store.get_strategy_defaults("user1", "sma_crossover") == {"short_window": 10}
        assert store.get_strategy_defaults("user2", "sma_crossover") == {"short_window": 20}
        store.close()

    def test_delete_strategy_defaults(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_strategy_defaults("user1", "sma_crossover", {"short_window": 15})
        store.delete_strategy_defaults("user1", "sma_crossover")

        assert store.get_strategy_defaults("user1", "sma_crossover") is None
        store.close()

    def test_no_preferred_returns_none(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        assert store.get_preferred_strategy("user1") is None
        store.close()


class TestRiskDefaults:
    def test_save_and_get_risk_defaults(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        settings: dict[str, int | float | bool] = {
            "max_position": 20,
            "max_daily_loss": 1000.0,
            "require_stop_loss": False,
        }
        store.save_risk_defaults("user1", settings)

        result = store.get_risk_defaults("user1")
        assert result == settings
        store.close()

    def test_upsert_overwrites_risk(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_risk_defaults("user1", {"max_position": 10})
        store.save_risk_defaults("user1", {"max_position": 25})

        result = store.get_risk_defaults("user1")
        assert result == {"max_position": 25}
        store.close()

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        assert store.get_risk_defaults("user1") is None
        store.close()

    def test_risk_defaults_scoped_by_user(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_risk_defaults("user1", {"max_position": 10})
        store.save_risk_defaults("user2", {"max_position": 50})

        assert store.get_risk_defaults("user1") == {"max_position": 10}
        assert store.get_risk_defaults("user2") == {"max_position": 50}
        store.close()

    def test_delete_risk_defaults(self, tmp_path: Path) -> None:
        store = UserDefaultsStore(tmp_path / "test.db")

        store.save_risk_defaults("user1", {"max_position": 10})
        store.delete_risk_defaults("user1")

        assert store.get_risk_defaults("user1") is None
        store.close()
