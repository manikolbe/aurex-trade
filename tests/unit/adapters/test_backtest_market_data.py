"""Tests for the HistoricalMarketDataAdapter — cursor-based bar replay."""

from datetime import UTC, datetime, timedelta

import pytest

from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.domain.models import BarData


def _make_bars(count: int) -> list[BarData]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        BarData(
            timestamp=start + timedelta(minutes=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0,
            symbol="TEST",
        )
        for i in range(count)
    ]


class TestHistoricalMarketDataAdapter:
    def test_raises_if_insufficient_bars(self) -> None:
        bars = _make_bars(5)
        with pytest.raises(ValueError, match="Need at least 10 bars"):
            HistoricalMarketDataAdapter(bars, bar_count=10)

    def test_get_latest_bars_returns_correct_count(self) -> None:
        bars = _make_bars(20)
        adapter = HistoricalMarketDataAdapter(bars, bar_count=10)
        result = adapter.get_latest_bars("TEST", 10)
        assert len(result) == 10
        # Should be the first 10 bars (cursor starts at bar_count)
        assert result[0].close == bars[0].close
        assert result[-1].close == bars[9].close

    def test_advance_moves_cursor(self) -> None:
        bars = _make_bars(20)
        adapter = HistoricalMarketDataAdapter(bars, bar_count=10)

        assert adapter.advance() is True
        result = adapter.get_latest_bars("TEST", 10)
        # After advance, window shifts by 1
        assert result[-1].close == bars[10].close

    def test_advance_returns_false_when_exhausted(self) -> None:
        bars = _make_bars(12)
        adapter = HistoricalMarketDataAdapter(bars, bar_count=10)
        # Total steps = 12 - 10 = 2
        assert adapter.advance() is True
        assert adapter.advance() is True
        assert adapter.advance() is False

    def test_is_exhausted(self) -> None:
        bars = _make_bars(11)
        adapter = HistoricalMarketDataAdapter(bars, bar_count=10)
        assert adapter.is_exhausted is False
        adapter.advance()
        assert adapter.is_exhausted is True

    def test_current_bar(self) -> None:
        bars = _make_bars(15)
        adapter = HistoricalMarketDataAdapter(bars, bar_count=10)
        assert adapter.current_bar == bars[9]
        adapter.advance()
        assert adapter.current_bar == bars[10]

    def test_total_steps(self) -> None:
        bars = _make_bars(60)
        adapter = HistoricalMarketDataAdapter(bars, bar_count=10)
        assert adapter.total_steps == 50
