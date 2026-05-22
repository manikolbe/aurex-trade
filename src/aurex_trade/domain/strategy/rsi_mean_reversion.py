"""RSI Mean-Reversion strategy — generates signals from RSI overbought/oversold crossings."""

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata
from aurex_trade.domain.strategy.indicators import calculate_atr, calculate_take_profit


class RSIMeanReversion:
    """Relative Strength Index mean-reversion strategy.

    Generates a LONG signal when RSI crosses below the oversold threshold
    (expecting price to revert upward), and a SHORT signal when RSI crosses
    above the overbought threshold (expecting price to revert downward).
    Returns None when there is insufficient data or no crossing.

    Stop-loss is calculated using ATR (Average True Range) to adapt to
    current market volatility.
    """

    def __init__(
        self,
        period: int = 14,
        overbought: int = 70,
        oversold: int = 30,
        atr_multiplier: float = 2.0,
        atr_period: int = 14,
        reward_ratio: float = 1.5,
    ) -> None:
        self._period = period
        self._overbought = overbought
        self._oversold = oversold
        self._atr_multiplier = atr_multiplier
        self._atr_period = atr_period
        self._reward_ratio = reward_ratio

    @property
    def name(self) -> str:
        return "rsi_mean_reversion"

    @property
    def min_bars(self) -> int:
        return max(self._period + 2, self._atr_period + 1)

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="RSI Mean Reversion",
            description=(
                "A mean-reversion strategy that uses the Relative Strength Index "
                "(RSI) to identify when an asset is overbought or oversold. "
                "When RSI drops below the oversold threshold, it signals that "
                "selling pressure is exhausted and price may bounce (buy). "
                "When RSI rises above the overbought threshold, it signals that "
                "buying pressure is exhausted and price may fall (sell). "
                "Works best in ranging/sideways markets; may give false signals "
                "during strong trends."
            ),
            params=(
                ParamMeta(
                    key="period",
                    label="RSI Period",
                    tooltip=(
                        "Number of bars used to calculate RSI. Standard is 14. "
                        "Shorter periods make RSI more sensitive (more signals, "
                        "more noise). Longer periods make it smoother."
                    ),
                    default=14,
                    min_value=2,
                    max_value=50,
                ),
                ParamMeta(
                    key="overbought",
                    label="Overbought Level",
                    tooltip=(
                        "RSI level above which the asset is considered overbought "
                        "(triggers SHORT signal on crossing). Standard is 70. "
                        "Higher values produce fewer but stronger signals."
                    ),
                    default=70,
                    min_value=50,
                    max_value=95,
                ),
                ParamMeta(
                    key="oversold",
                    label="Oversold Level",
                    tooltip=(
                        "RSI level below which the asset is considered oversold "
                        "(triggers LONG signal on crossing). Standard is 30. "
                        "Lower values produce fewer but stronger signals."
                    ),
                    default=30,
                    min_value=5,
                    max_value=50,
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
                    min_value=1.0,
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
                ParamMeta(
                    key="reward_ratio",
                    label="Reward Ratio",
                    tooltip=(
                        "Controls where the trade automatically closes for "
                        "profit. Your stop-loss is set automatically based on "
                        "market volatility (ATR Multiplier above). The ratio "
                        "decides how much further to place take-profit. "
                        "Example: if stop-loss ends up 10 points below entry, "
                        "a ratio of 1.5 places take-profit 15 points above. "
                        "Both levels appear on the chart when a trade opens. "
                        "Set to 0 to never auto-close winners."
                    ),
                    default=1.5,
                    min_value=0.0,
                    max_value=5.0,
                ),
            ),
        )

    def generate(self, bars: list[BarData]) -> Signal | None:
        # Need period + 2 closes for two RSI values (to detect crossing)
        min_bars = self._period + 2
        if len(bars) < min_bars:
            return None

        closes = [bar.close for bar in bars]

        # Compute RSI for the last two positions to detect crossing
        prev_rsi = _calculate_rsi(closes[:-1], self._period)
        curr_rsi = _calculate_rsi(closes, self._period)

        if prev_rsi is None or curr_rsi is None:
            return None

        signal_type: SignalType | None = None

        # LONG: RSI crosses below oversold (was at/above, now below)
        if prev_rsi >= self._oversold and curr_rsi < self._oversold:
            signal_type = SignalType.LONG
        # SHORT: RSI crosses above overbought (was at/below, now above)
        elif prev_rsi <= self._overbought and curr_rsi > self._overbought:
            signal_type = SignalType.SHORT

        if signal_type is None:
            return None

        latest = bars[-1]
        entry_price = latest.close
        atr = calculate_atr(bars, self._atr_period)

        # Calculate stop-loss based on ATR (clamped to sensible bounds)
        stop_loss: float | None = None
        take_profit: float | None = None
        if atr > 0:
            stop_distance = self._atr_multiplier * atr
            if signal_type == SignalType.LONG:
                stop_loss = max(0.0, entry_price - stop_distance)
            else:
                stop_loss = entry_price + stop_distance
            take_profit = calculate_take_profit(
                entry_price, stop_distance, self._reward_ratio, signal_type,
            )

        # Strength: distance from threshold normalized to 0-1
        if signal_type == SignalType.LONG:
            strength = (self._oversold - curr_rsi) / self._oversold if self._oversold > 0 else 0.0
        else:
            strength = (
                (curr_rsi - self._overbought) / (100 - self._overbought)
                if self._overbought < 100
                else 0.0
            )

        return Signal(
            symbol=latest.symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=max(0.0, min(1.0, strength)),
            metadata={
                "rsi": f"{curr_rsi:.2f}",
                "prev_rsi": f"{prev_rsi:.2f}",
                "entry_price": f"{entry_price:.5f}",
                "atr": f"{atr:.5f}",
            },
            stop_loss=stop_loss,
            take_profit=take_profit,
        )


def _calculate_rsi(closes: list[float], period: int) -> float | None:
    """Calculate RSI using Wilder's smoothing method.

    Requires at least period + 1 closes. Returns None if insufficient data.
    """
    if len(closes) < period + 1:
        return None

    # Calculate price changes
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # First average: simple average of first `period` changes
    first_gains = [max(0.0, c) for c in changes[:period]]
    first_losses = [max(0.0, -c) for c in changes[:period]]
    avg_gain = sum(first_gains) / period
    avg_loss = sum(first_losses) / period

    # Wilder's smoothing for remaining changes
    for change in changes[period:]:
        gain = max(0.0, change)
        loss = max(0.0, -change)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
