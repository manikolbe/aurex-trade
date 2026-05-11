"""Repository port — defines the contract for persistence operations."""

from typing import Protocol

from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class RepositoryPort(Protocol):
    """Port for persisting and retrieving trading data.

    Any persistence adapter (SQLite, PostgreSQL, etc.) must satisfy this interface.
    All methods require a user_id for multi-tenant isolation.
    """

    def save_signal(self, signal: Signal, *, user_id: str) -> None: ...

    def save_decision(self, decision: RiskDecision, *, user_id: str) -> None: ...

    def save_trade(self, trade: Trade, *, user_id: str) -> None: ...

    def save_position(self, position: Position, *, user_id: str) -> None: ...

    def get_trades_today(self, symbol: str, *, user_id: str) -> list[Trade]: ...

    def get_current_position(self, symbol: str, *, user_id: str) -> Position | None: ...
