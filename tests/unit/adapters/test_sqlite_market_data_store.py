"""Tests for SQLiteMarketDataStore — bar persistence via SQLite."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from aurex_trade.adapters.sqlite.market_data_store import SQLiteMarketDataStore
from aurex_trade.domain.models import BarData


def _make_bars(count: int, start: datetime | None = None) -> list[BarData]:
    if start is None:
        start = datetime(2025, 3, 1, tzinfo=UTC)
    return [
        BarData(
            timestamp=start + timedelta(minutes=i),
            open=100.0 + i * 0.1,
            high=101.0 + i * 0.1,
            low=99.0 + i * 0.1,
            close=100.5 + i * 0.1,
            volume=1000.0 + i,
            symbol="XAU_USD",
        )
        for i in range(count)
    ]


class TestSQLiteMarketDataStore:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)
        bars = _make_bars(10)

        store.save_bars(bars, "XAU_USD", "M1")

        loaded = store.load_bars("XAU_USD", "M1")
        assert len(loaded) == 10
        assert loaded[0].timestamp == bars[0].timestamp
        assert loaded[0].close == bars[0].close
        assert loaded[-1].symbol == "XAU_USD"
        store.close()

    def test_load_with_date_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)
        bars = _make_bars(100)
        store.save_bars(bars, "XAU_USD", "M1")

        start = datetime(2025, 3, 1, 0, 30, tzinfo=UTC)
        end = datetime(2025, 3, 1, 0, 59, tzinfo=UTC)

        filtered = store.load_bars("XAU_USD", "M1", start=start, end=end)
        assert len(filtered) == 30
        assert all(start <= b.timestamp <= end for b in filtered)
        store.close()

    def test_get_date_range(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)
        bars = _make_bars(60)
        store.save_bars(bars, "XAU_USD", "M1")

        result = store.get_date_range("XAU_USD", "M1")
        assert result is not None
        assert result[0] == bars[0].timestamp
        assert result[1] == bars[-1].timestamp
        store.close()

    def test_get_date_range_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)

        result = store.get_date_range("XAU_USD", "M1")
        assert result is None
        store.close()

    def test_insert_or_ignore_deduplication(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)
        bars = _make_bars(10)

        store.save_bars(bars, "XAU_USD", "M1")
        store.save_bars(bars, "XAU_USD", "M1")  # Same bars again

        loaded = store.load_bars("XAU_USD", "M1")
        assert len(loaded) == 10  # No duplicates
        store.close()

    def test_overlapping_ranges_merge(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)

        start1 = datetime(2025, 3, 1, tzinfo=UTC)
        bars1 = _make_bars(10, start=start1)
        store.save_bars(bars1, "XAU_USD", "M1")

        # Overlapping range (starts at minute 5, extends to minute 14)
        start2 = datetime(2025, 3, 1, 0, 5, tzinfo=UTC)
        bars2 = _make_bars(10, start=start2)
        store.save_bars(bars2, "XAU_USD", "M1")

        loaded = store.load_bars("XAU_USD", "M1")
        assert len(loaded) == 15  # 0-14 minutes, no duplicates
        store.close()

    def test_preserves_precision(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)
        bar = BarData(
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
            open=4572.36123,
            high=4573.99456,
            low=4571.00789,
            close=4572.85012,
            volume=12345.678,
            symbol="XAU_USD",
        )
        store.save_bars([bar], "XAU_USD", "H1")
        loaded = store.load_bars("XAU_USD", "H1")
        assert loaded[0].open == bar.open
        assert loaded[0].close == bar.close
        assert loaded[0].volume == bar.volume
        store.close()

    def test_separate_symbol_granularity(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)

        bars_m1 = _make_bars(5)
        bars_h1 = _make_bars(3)
        store.save_bars(bars_m1, "XAU_USD", "M1")
        store.save_bars(bars_h1, "XAU_USD", "H1")

        assert len(store.load_bars("XAU_USD", "M1")) == 5
        assert len(store.load_bars("XAU_USD", "H1")) == 3
        assert len(store.load_bars("EUR_USD", "M1")) == 0
        store.close()

    def test_concurrent_writes_same_db(self, tmp_path: Path) -> None:
        """Two store instances writing different symbols to the same DB."""
        db = tmp_path / "test.db"
        store1 = SQLiteMarketDataStore(db)
        store2 = SQLiteMarketDataStore(db)

        bars1 = _make_bars(10)
        bars2 = _make_bars(10)

        store1.save_bars(bars1, "XAU_USD", "M1")
        store2.save_bars(bars2, "EUR_USD", "M1")

        # Both should be readable from either store
        assert len(store1.load_bars("XAU_USD", "M1")) == 10
        assert len(store1.load_bars("EUR_USD", "M1")) == 10
        store1.close()
        store2.close()

    def test_empty_bars_noop(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = SQLiteMarketDataStore(db)
        store.save_bars([], "XAU_USD", "M1")  # Should not raise
        assert store.load_bars("XAU_USD", "M1") == []
        store.close()
