"""Broker port — defines the contract for order execution and position queries."""

from typing import Protocol
from uuid import UUID

from aurex_trade.domain.models import (
    ClosedTradeInfo,
    OpenBrokerTrade,
    Order,
    PendingOrder,
    Position,
    Trade,
)


class BrokerPort(Protocol):
    """Port for placing orders and querying positions.

    Any broker adapter (paper, OANDA, etc.) must satisfy this interface.
    """

    @property
    def equity(self) -> float: ...

    def place_order(self, order: Order) -> Trade: ...

    def cancel_order(self, order_id: UUID) -> bool: ...

    def get_positions(self, symbol: str) -> Position | None: ...

    def get_open_trades(self, symbol: str) -> list[OpenBrokerTrade]: ...

    def get_closed_trade_details(self, broker_trade_id: str) -> ClosedTradeInfo | None: ...

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]: ...

    def cancel_all_orders(self, symbol: str) -> int: ...

    def cancel_pending_order(self, broker_order_id: str) -> bool: ...

    def close_trade(self, broker_trade_id: str) -> None: ...

    def set_trailing_stop(self, broker_trade_id: str, distance: float) -> None: ...
