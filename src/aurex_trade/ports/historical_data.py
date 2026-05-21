"""Historical data port — defines the contract for persisting price bars."""

from datetime import datetime
from typing import Protocol

from aurex_trade.domain.models import BarData


class HistoricalDataPort(Protocol):
    """Port for storing and retrieving historical OHLCV bar data.

    Market data is shared across all users (no user_id scoping).
    Implementations must handle concurrent writes safely.
    """

    def save_bars(self, bars: list[BarData], symbol: str, granularity: str) -> None:
        """Persist bars. Duplicate timestamps are silently ignored."""
        ...

    def load_bars(
        self,
        symbol: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[BarData]:
        """Load bars, optionally filtered by date range. Returns sorted by timestamp."""
        ...

    def get_date_range(self, symbol: str, granularity: str) -> tuple[datetime, datetime] | None:
        """Return (min_timestamp, max_timestamp) for stored data, or None if empty."""
        ...
