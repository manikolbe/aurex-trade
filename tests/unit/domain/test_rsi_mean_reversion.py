"""Tests for the RSI Mean-Reversion strategy."""

from datetime import UTC, datetime, timedelta

import pytest

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData
from aurex_trade.domain.strategy.rsi_mean_reversion import RSIMeanReversion


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


class TestRSIMeanReversion:
    """Tests for RSIMeanReversion with period=5, overbought=70, oversold=30."""

    def setup_method(self) -> None:
        self.strategy = RSIMeanReversion(
            period=5, overbought=70, oversold=30, atr_multiplier=2.0, atr_period=3
        )

    def test_name(self) -> None:
        assert self.strategy.name == "rsi_mean_reversion"

    def test_insufficient_data_returns_none(self) -> None:
        """Need period + 2 = 7 bars minimum; 6 bars should return None."""
        bars = _make_bars([100.0] * 6)
        assert self.strategy.generate(bars) is None

    def test_empty_bars_returns_none(self) -> None:
        assert self.strategy.generate([]) is None

    def test_flat_market_returns_none(self) -> None:
        """Flat prices produce no gains/losses — RSI undefined, no crossing."""
        bars = _make_bars([100.0] * 20)
        assert self.strategy.generate(bars) is None

    def test_oversold_crossing_long_signal(self) -> None:
        """RSI crossing below oversold threshold → LONG signal.

        With period=5: prev_rsi=44.4 (>=30), curr_rsi=28.6 (<30) → crossing.
        """
        # Mixed prices give moderate RSI, then big drop pushes below 30
        prices = [100.0, 102.0, 101.0, 103.0, 100.0, 99.0, 95.0]
        bars = _make_bars(prices)
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.symbol == "GLD"
        assert signal.strategy_name == "rsi_mean_reversion"

    def test_overbought_crossing_short_signal(self) -> None:
        """RSI crossing above overbought threshold → SHORT signal.

        With period=5: prev_rsi=57.1 (<=70), curr_rsi=75.0 (>70) → crossing.
        """
        # Mixed prices give moderate RSI, then big rise pushes above 70
        prices = [100.0, 99.0, 101.0, 100.0, 102.0, 101.0, 105.0]
        bars = _make_bars(prices)
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.SHORT

    def test_no_crossing_neutral_zone_returns_none(self) -> None:
        """RSI stays in neutral zone (between 30-70) → None."""
        # Gentle oscillation that keeps RSI in the middle
        prices = [100.0, 100.5, 100.0, 100.5, 100.0, 100.5, 100.0]
        bars = _make_bars(prices)
        assert self.strategy.generate(bars) is None

    def test_already_oversold_no_crossing_returns_none(self) -> None:
        """RSI already below oversold on both bars (no crossing) → None."""
        # Monotonic decline: RSI=0 on both bars (already below 30, not crossing)
        prices = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0]
        bars = _make_bars(prices)
        assert self.strategy.generate(bars) is None

    def test_already_overbought_no_crossing_returns_none(self) -> None:
        """RSI already above overbought on both bars (no crossing) → None."""
        # Monotonic rise: RSI=100 on both bars (already above 70, not crossing)
        prices = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0]
        bars = _make_bars(prices)
        assert self.strategy.generate(bars) is None

    def test_signal_has_rsi_metadata(self) -> None:
        """Signal metadata should include RSI value and entry price."""
        prices = [100.0, 102.0, 101.0, 103.0, 100.0, 99.0, 95.0]
        bars = _make_bars(prices)
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert "rsi" in signal.metadata
        assert "prev_rsi" in signal.metadata
        assert "entry_price" in signal.metadata
        assert "atr" in signal.metadata

    def test_signal_strength_bounded(self) -> None:
        """Signal strength should be between 0 and 1."""
        prices = [100.0, 102.0, 101.0, 103.0, 100.0, 99.0, 95.0]
        bars = _make_bars(prices)
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert 0.0 <= signal.strength <= 1.0


@pytest.mark.parametrize(
    ("period", "overbought", "oversold"),
    [(7, 80, 20), (14, 70, 30), (3, 75, 25)],
)
def test_different_parameter_sizes(
    period: int, overbought: int, oversold: int
) -> None:
    """Strategy works with various parameter combinations."""
    strategy = RSIMeanReversion(
        period=period, overbought=overbought, oversold=oversold
    )
    flat_bars = _make_bars([100.0] * (period + 10))
    assert strategy.generate(flat_bars) is None


def _oversold_bars_with_spread() -> list[BarData]:
    """Bars that trigger a LONG signal (oversold crossing) with realistic OHLC.

    Closes: [100, 102, 101, 103, 100, 99, 95] → prev_rsi=44.4, curr_rsi=28.6
    """
    closes = [100.0, 102.0, 101.0, 103.0, 100.0, 99.0, 95.0]
    bars: list[BarData] = []
    for i, c in enumerate(closes):
        bars.append(
            BarData(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
                open=c - 0.5,
                high=c + 1.0,
                low=c - 1.0,
                close=c,
                volume=100.0,
                symbol="GLD",
            )
        )
    return bars


def _overbought_bars_with_spread() -> list[BarData]:
    """Bars that trigger a SHORT signal (overbought crossing) with realistic OHLC.

    Closes: [100, 99, 101, 100, 102, 101, 105] → prev_rsi=57.1, curr_rsi=75.0
    """
    closes = [100.0, 99.0, 101.0, 100.0, 102.0, 101.0, 105.0]
    bars: list[BarData] = []
    for i, c in enumerate(closes):
        bars.append(
            BarData(
                timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
                open=c + 0.5,
                high=c + 1.0,
                low=c - 1.0,
                close=c,
                volume=100.0,
                symbol="GLD",
            )
        )
    return bars


class TestStopLossCalculation:
    """Tests for ATR-based stop-loss on RSI signals."""

    def test_long_signal_stop_loss_below_entry(self) -> None:
        """LONG signal stop-loss should be below entry price."""
        strategy = RSIMeanReversion(
            period=5, overbought=70, oversold=30, atr_multiplier=2.0, atr_period=3
        )
        bars = _oversold_bars_with_spread()
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.stop_loss is not None
        assert signal.stop_loss < float(signal.metadata["entry_price"])

    def test_short_signal_stop_loss_above_entry(self) -> None:
        """SHORT signal stop-loss should be above entry price."""
        strategy = RSIMeanReversion(
            period=5, overbought=70, oversold=30, atr_multiplier=2.0, atr_period=3
        )
        bars = _overbought_bars_with_spread()
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.SHORT
        assert signal.stop_loss is not None
        assert signal.stop_loss > float(signal.metadata["entry_price"])

    def test_stop_loss_none_when_insufficient_bars_for_atr(self) -> None:
        """When not enough bars for ATR period, stop_loss should be None."""
        # atr_period=50 needs 51 bars, but we only have 7
        strategy = RSIMeanReversion(
            period=5, overbought=70, oversold=30, atr_multiplier=2.0, atr_period=50
        )
        # This triggers a LONG signal (oversold crossing) but has too few bars for ATR
        prices = [100.0, 102.0, 101.0, 103.0, 100.0, 99.0, 95.0]
        bars = _make_bars(prices)
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.stop_loss is None
