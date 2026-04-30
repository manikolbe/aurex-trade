"""Broker port — defines the contract for order execution and position queries."""

from typing import Protocol
from uuid import UUID

from aurex_trade.domain.models import Order, Position, Trade


class BrokerPort(Protocol):
    """Port for placing orders and querying positions.

    Any broker adapter (paper, OANDA, etc.) must satisfy this interface.
    """

    @property
    def equity(self) -> float: ...

    def place_order(self, order: Order) -> Trade: ...

    def cancel_order(self, order_id: UUID) -> bool: ...

    def get_positions(self, symbol: str) -> Position | None: ...
