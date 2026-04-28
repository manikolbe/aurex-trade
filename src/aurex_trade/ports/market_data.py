"""Market data port — defines the contract for price feed access."""

from typing import Protocol

from aurex_trade.domain.models import BarData


class MarketDataPort(Protocol):
    """Port for fetching historical price bars.

    Any market data adapter (paper, OANDA, etc.) must satisfy this interface.
    """

    def get_latest_bars(self, symbol: str, count: int) -> list[BarData]: ...
