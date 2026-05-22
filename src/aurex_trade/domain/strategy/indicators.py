"""Shared technical indicators used by multiple strategies."""

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData


def calculate_atr(bars: list[BarData], period: int) -> float:
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


def calculate_take_profit(
    entry_price: float,
    stop_distance: float,
    reward_ratio: float,
    signal_type: SignalType,
) -> float | None:
    """Calculate take-profit price from entry, stop distance, and reward ratio.

    Returns None if reward_ratio is zero (TP disabled).
    """
    if reward_ratio <= 0:
        return None
    if signal_type == SignalType.LONG:
        return entry_price + (reward_ratio * stop_distance)
    return max(0.0, entry_price - (reward_ratio * stop_distance))
