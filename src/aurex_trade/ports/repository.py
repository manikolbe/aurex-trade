"""Repository port — defines the contract for persistence operations."""

from typing import Protocol

from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class RepositoryPort(Protocol):
    """Port for persisting and retrieving trading data.

    Any persistence adapter (SQLite, PostgreSQL, etc.) must satisfy this interface.
    """

    def save_signal(self, signal: Signal) -> None: ...

    def save_decision(self, decision: RiskDecision) -> None: ...

    def save_trade(self, trade: Trade) -> None: ...

    def save_position(self, position: Position) -> None: ...

    def get_trades_today(self, symbol: str) -> list[Trade]: ...

    def get_current_position(self, symbol: str) -> Position | None: ...
