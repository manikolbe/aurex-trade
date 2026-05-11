"""Tests for HistoricalDataStore — CSV round-trip."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
from aurex_trade.domain.models import BarData


def _make_bars(count: int) -> list[BarData]:
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


class TestHistoricalDataStore:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        store = HistoricalDataStore(tmp_path)
        bars = _make_bars(10)

        store.save_bars(bars, "XAU_USD", "M1")

        loaded = store.load_bars("XAU_USD", "M1")
        assert len(loaded) == 10
        assert loaded[0].timestamp == bars[0].timestamp
        assert loaded[0].close == bars[0].close
        assert loaded[-1].symbol == "XAU_USD"

    def test_load_with_date_filter(self, tmp_path: Path) -> None:
        store = HistoricalDataStore(tmp_path)
        bars = _make_bars(100)
        store.save_bars(bars, "XAU_USD", "M1")

        start = datetime(2025, 3, 1, 0, 30, tzinfo=UTC)
        end = datetime(2025, 3, 1, 0, 59, tzinfo=UTC)

        filtered = store.load_bars("XAU_USD", "M1", start=start, end=end)
        assert len(filtered) == 30
        assert all(start <= b.timestamp <= end for b in filtered)

    def test_load_nonexistent_file_raises(self, tmp_path: Path) -> None:
        store = HistoricalDataStore(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.load_bars("MISSING", "M1")

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "dir"
        store = HistoricalDataStore(nested)
        bars = _make_bars(5)
        store.save_bars(bars, "XAU_USD", "M1")
        loaded = store.load_bars("XAU_USD", "M1")
        assert len(loaded) == 5

    def test_get_date_range(self, tmp_path: Path) -> None:
        store = HistoricalDataStore(tmp_path)
        bars = _make_bars(60)
        store.save_bars(bars, "XAU_USD", "M1")

        result = store.get_date_range("XAU_USD", "M1")
        assert result is not None
        assert result[0] == bars[0].timestamp
        assert result[1] == bars[-1].timestamp

    def test_get_date_range_no_data(self, tmp_path: Path) -> None:
        store = HistoricalDataStore(tmp_path)
        assert store.get_date_range("MISSING", "M1") is None

    def test_preserves_precision(self, tmp_path: Path) -> None:
        store = HistoricalDataStore(tmp_path)
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
