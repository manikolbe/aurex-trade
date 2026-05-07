"""Shared technical indicators used by multiple strategies."""

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
