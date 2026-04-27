"""Tests for the SMA Crossover strategy."""

from datetime import UTC, datetime, timedelta

import pytest

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData
from aurex_trade.domain.strategy.sma_crossover import SMACrossover


def _make_bars(closes: list[float], symbol: str = "GLD") -> list[BarData]:
    """Build a list of BarData from close prices (open=high=low=close for simplicity)."""
    return [
        BarData(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=100.0,
            symbol=symbol,
        )
        for i, c in enumerate(closes)
    ]


class TestSMACrossover:
    """Tests for SMACrossover strategy with short_window=3, long_window=5."""

    def setup_method(self) -> None:
        self.strategy = SMACrossover(short_window=3, long_window=5)

    def test_name(self) -> None:
        assert self.strategy.name == "sma_crossover"

    def test_insufficient_data_returns_none(self) -> None:
        """Need long_window + 1 = 6 bars minimum; 5 bars should return None."""
        bars = _make_bars([100.0, 100.0, 100.0, 100.0, 100.0])
        assert self.strategy.generate(bars) is None

    def test_exact_minimum_bars_flat_market(self) -> None:
        """Exactly long_window + 1 bars, all same price — no crossover."""
        bars = _make_bars([100.0] * 6)
        assert self.strategy.generate(bars) is None

    def test_crossover_up_long_signal(self) -> None:
        """Short SMA crosses above long SMA → LONG signal.

        Setup: 5 bars at 100 (establishes long SMA = 100, short SMA <= 100),
        then a spike so short SMA > long SMA on the last bar.
        """
        # Bars: 5 at 100.0, then 1 at 110.0 = 6 total
        # Previous short SMA (bars 2-4): avg(100, 100, 100) = 100
        # Previous long SMA (bars 0-4): avg(100, 100, 100, 100, 100) = 100
        # Current short SMA (bars 3-5): avg(100, 100, 110) = 103.33
        # Current long SMA (bars 1-5): avg(100, 100, 100, 100, 110) = 102
        # prev_short <= prev_long AND curr_short > curr_long → LONG
        bars = _make_bars([100.0, 100.0, 100.0, 100.0, 100.0, 110.0])
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.symbol == "GLD"
        assert signal.strategy_name == "sma_crossover"

    def test_crossover_down_short_signal(self) -> None:
        """Short SMA crosses below long SMA → SHORT signal.

        Setup: 5 bars at 100, then a drop so short SMA < long SMA.
        """
        # Bars: 5 at 100.0, then 1 at 90.0 = 6 total
        # Previous short SMA (bars 2-4): avg(100, 100, 100) = 100
        # Previous long SMA (bars 0-4): avg(100, 100, 100, 100, 100) = 100
        # Current short SMA (bars 3-5): avg(100, 100, 90) = 96.67
        # Current long SMA (bars 1-5): avg(100, 100, 100, 100, 90) = 98
        # prev_short >= prev_long AND curr_short < curr_long → SHORT
        bars = _make_bars([100.0, 100.0, 100.0, 100.0, 100.0, 90.0])
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.SHORT

    def test_no_crossover_returns_none(self) -> None:
        """Short SMA already above long SMA — no new crossover → None."""
        # Gradually rising prices — short SMA stays above long SMA
        bars = _make_bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        signal = self.strategy.generate(bars)
        assert signal is None

    def test_signal_has_sma_metadata(self) -> None:
        """Signal metadata should include short and long SMA values."""
        bars = _make_bars([100.0, 100.0, 100.0, 100.0, 100.0, 110.0])
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert "short_sma" in signal.metadata
        assert "long_sma" in signal.metadata

    def test_signal_strength_is_positive(self) -> None:
        """Signal strength should be a positive float representing divergence."""
        bars = _make_bars([100.0, 100.0, 100.0, 100.0, 100.0, 110.0])
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert signal.strength > 0.0

    def test_empty_bars_returns_none(self) -> None:
        assert self.strategy.generate([]) is None


@pytest.mark.parametrize(
    ("short_window", "long_window"),
    [(5, 10), (10, 30), (2, 4)],
)
def test_different_window_sizes(short_window: int, long_window: int) -> None:
    """Strategy works with various window sizes."""
    strategy = SMACrossover(short_window=short_window, long_window=long_window)
    flat_bars = _make_bars([100.0] * (long_window + 2))
    assert strategy.generate(flat_bars) is None
