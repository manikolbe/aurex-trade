"""SMA Crossover strategy — generates signals from short/long moving average crossovers."""

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal


class SMACrossover:
    """Simple Moving Average crossover strategy.

    Generates a LONG signal when the short SMA crosses above the long SMA,
    and a SHORT signal when the short SMA crosses below the long SMA.
    Returns None when there is insufficient data or no crossover.

    Stop-loss is calculated using ATR (Average True Range) to adapt to
    current market volatility.
    """

    def __init__(
        self,
        short_window: int,
        long_window: int,
        atr_multiplier: float = 2.0,
        atr_period: int = 14,
    ) -> None:
        self._short_window = short_window
        self._long_window = long_window
        self._atr_multiplier = atr_multiplier
        self._atr_period = atr_period

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
        entry_price = latest.close
        atr = _calculate_atr(bars, self._atr_period)

        # Calculate stop-loss based on ATR (clamped to sensible bounds)
        stop_loss: float | None = None
        if atr > 0:
            if signal_type == SignalType.LONG:
                stop_loss = max(0.0, entry_price - (self._atr_multiplier * atr))
            else:
                stop_loss = entry_price + (self._atr_multiplier * atr)

        return Signal(
            symbol=latest.symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=abs(curr_short - curr_long) / curr_long if curr_long != 0 else 0.0,
            metadata={
                "short_sma": f"{curr_short:.4f}",
                "long_sma": f"{curr_long:.4f}",
                "entry_price": f"{entry_price:.5f}",
                "atr": f"{atr:.5f}",
            },
            stop_loss=stop_loss,
        )


def _sma(values: list[float], window: int) -> float:
    """Compute simple moving average over the last `window` values."""
    return sum(values[-window:]) / window


def _calculate_atr(bars: list[BarData], period: int) -> float:
    """Calculate Average True Range over the given period.

    ATR measures market volatility using the maximum of:
    - Current high - current low
    - |Current high - previous close|
    - |Current low - previous close|

    Returns 0.0 if insufficient bars are available.
    """
    if len(bars) < period + 1:
        return 0.0

    true_ranges: list[float] = []
    for i in range(-period, 0):
        high = bars[i].high
        low = bars[i].low
        prev_close = bars[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    return sum(true_ranges) / len(true_ranges)
