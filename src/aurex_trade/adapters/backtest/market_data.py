"""Historical market data adapter — cursor-based bar replay for backtesting.

Satisfies MarketDataPort by serving pre-loaded bars sequentially.
"""

from __future__ import annotations

from aurex_trade.domain.models import BarData


class HistoricalMarketDataAdapter:
    """Replays historical bars through the MarketDataPort interface.

    The cursor starts at position `bar_count` (so the strategy always
    gets a full window on the first call) and advances one step at a time.
    """

    def __init__(self, bars: list[BarData], bar_count: int = 50) -> None:
        if len(bars) < bar_count:
            msg = f"Need at least {bar_count} bars for replay, got {len(bars)}"
            raise ValueError(msg)

        self._bars = bars
        self._bar_count = bar_count
        self._cursor = bar_count  # Start so first get_latest_bars returns full window

    def get_latest_bars(self, symbol: str, count: int) -> list[BarData]:
        """Return the most recent `count` bars up to the current cursor.

        Satisfies MarketDataPort Protocol.
        """
        start = max(0, self._cursor - count)
        return self._bars[start : self._cursor]

    def advance(self) -> bool:
        """Move the cursor forward by one bar.

        Returns:
            True if advanced successfully, False if exhausted.
        """
        if self._cursor >= len(self._bars):
            return False
        self._cursor += 1
        return True

    @property
    def is_exhausted(self) -> bool:
        """True when all bars have been replayed."""
        return self._cursor >= len(self._bars)

    @property
    def current_bar(self) -> BarData:
        """The bar at the current cursor position (most recent bar visible)."""
        return self._bars[self._cursor - 1]

    @property
    def total_steps(self) -> int:
        """Total number of steps the replay will take."""
        return len(self._bars) - self._bar_count
