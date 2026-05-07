"""SMA Crossover strategy — generates signals from short/long moving average crossovers."""

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata
from aurex_trade.domain.strategy.indicators import calculate_atr


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

    @property
    def min_bars(self) -> int:
        return max(self._long_window + 1, self._atr_period + 1)

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="SMA Crossover",
            description=(
                "A trend-following strategy that uses two Simple Moving Averages "
                "(SMAs) of different lengths. The 'fast' average tracks recent price "
                "action while the 'slow' average captures the longer-term trend. "
                "When the fast average crosses above the slow average, it signals "
                "that upward momentum is building (buy). When it crosses below, "
                "downward momentum is building (sell). Works best in trending "
                "markets; may generate false signals in sideways/choppy conditions."
            ),
            params=(
                ParamMeta(
                    key="short_window",
                    label="Fast MA Window",
                    tooltip=(
                        "Number of bars the fast moving average looks back. "
                        "Smaller values react quicker to price changes but "
                        "produce more noise."
                    ),
                    default=10,
                    min_value=2,
                    max_value=100,
                ),
                ParamMeta(
                    key="long_window",
                    label="Slow MA Window",
                    tooltip=(
                        "Number of bars the slow moving average looks back. "
                        "Must be larger than the fast window. Larger values "
                        "give smoother signals but react slower to trend changes."
                    ),
                    default=30,
                    min_value=5,
                    max_value=500,
                ),
                ParamMeta(
                    key="atr_multiplier",
                    label="ATR Multiplier",
                    tooltip=(
                        "How many ATR units away to place the stop-loss. "
                        "Higher values give trades more room to breathe but "
                        "risk larger losses per trade."
                    ),
                    default=2.0,
                    min_value=0.5,
                    max_value=5.0,
                ),
                ParamMeta(
                    key="atr_period",
                    label="ATR Period",
                    tooltip=(
                        "Number of bars used to calculate Average True Range "
                        "(volatility). Longer periods smooth out volatility "
                        "spikes; shorter periods react faster."
                    ),
                    default=14,
                    min_value=2,
                    max_value=50,
                ),
            ),
        )

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
        atr = calculate_atr(bars, self._atr_period)

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


