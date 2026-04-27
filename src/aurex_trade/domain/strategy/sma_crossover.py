"""SMA Crossover strategy — generates signals from short/long moving average crossovers."""

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal


class SMACrossover:
    """Simple Moving Average crossover strategy.

    Generates a LONG signal when the short SMA crosses above the long SMA,
    and a SHORT signal when the short SMA crosses below the long SMA.
    Returns None when there is insufficient data or no crossover.
    """

    def __init__(self, short_window: int, long_window: int) -> None:
        self._short_window = short_window
        self._long_window = long_window

    @property
    def name(self) -> str:
        return "sma_crossover"

    def generate(self, bars: list[BarData]) -> Signal | None:
        # Need at least long_window + 1 bars to detect a crossover
        min_bars = self._long_window + 1
        if len(bars) < min_bars:
            return None

        closes = [bar.close for bar in bars]

        prev_short = _sma(closes[-(self._short_window + 1) : -1], self._short_window)
        prev_long = _sma(closes[-(self._long_window + 1) : -1], self._long_window)
        curr_short = _sma(closes[-self._short_window :], self._short_window)
        curr_long = _sma(closes[-self._long_window :], self._long_window)

        signal_type: SignalType | None = None

        if prev_short <= prev_long and curr_short > curr_long:
            signal_type = SignalType.LONG
        elif prev_short >= prev_long and curr_short < curr_long:
            signal_type = SignalType.SHORT

        if signal_type is None:
            return None

        latest = bars[-1]
        return Signal(
            symbol=latest.symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=abs(curr_short - curr_long) / curr_long if curr_long != 0 else 0.0,
            metadata={
                "short_sma": f"{curr_short:.4f}",
                "long_sma": f"{curr_long:.4f}",
            },
        )


def _sma(values: list[float], window: int) -> float:
    """Compute simple moving average over the last `window` values."""
    return sum(values[-window:]) / window
