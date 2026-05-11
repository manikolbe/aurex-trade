"""Tests for UserDataPreferencesStore — per-user date range preferences."""

from pathlib import Path

from aurex_trade.adapters.sqlite.market_data_store import UserDataPreferencesStore


class TestUserDataPreferencesStore:
    def test_save_and_get_preference(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = UserDataPreferencesStore(db)

        store.save_preference("user1", "XAU_USD", "M1", "2025-01-01", "2025-01-15")

        result = store.get_preference("user1", "XAU_USD", "M1")
        assert result == ("2025-01-01", "2025-01-15")
        store.close()

    def test_upsert_updates_existing(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = UserDataPreferencesStore(db)

        store.save_preference("user1", "XAU_USD", "M1", "2025-01-01", "2025-01-15")
        store.save_preference("user1", "XAU_USD", "M1", "2025-02-01", "2025-02-28")

        result = store.get_preference("user1", "XAU_USD", "M1")
        assert result == ("2025-02-01", "2025-02-28")
        store.close()

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = UserDataPreferencesStore(db)

        result = store.get_preference("user1", "XAU_USD", "M1")
        assert result is None
        store.close()

    def test_preferences_scoped_by_user(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = UserDataPreferencesStore(db)

        store.save_preference("user1", "XAU_USD", "M1", "2025-01-01", "2025-01-15")
        store.save_preference("user2", "XAU_USD", "M1", "2025-03-01", "2025-03-31")

        assert store.get_preference("user1", "XAU_USD", "M1") == ("2025-01-01", "2025-01-15")
        assert store.get_preference("user2", "XAU_USD", "M1") == ("2025-03-01", "2025-03-31")
        store.close()

    def test_preferences_scoped_by_symbol_granularity(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = UserDataPreferencesStore(db)

        store.save_preference("user1", "XAU_USD", "M1", "2025-01-01", "2025-01-15")
        store.save_preference("user1", "EUR_USD", "M1", "2025-02-01", "2025-02-15")
        store.save_preference("user1", "XAU_USD", "H1", "2025-03-01", "2025-03-15")

        assert store.get_preference("user1", "XAU_USD", "M1") == ("2025-01-01", "2025-01-15")
        assert store.get_preference("user1", "EUR_USD", "M1") == ("2025-02-01", "2025-02-15")
        assert store.get_preference("user1", "XAU_USD", "H1") == ("2025-03-01", "2025-03-15")
        store.close()
