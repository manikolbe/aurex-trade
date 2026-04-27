"""In-memory repository — stores all trading data in plain Python collections.

Used for local mode and integration tests. No external dependencies.
Data does not survive process restarts — use SQLite adapter for persistence.
"""

from datetime import UTC, datetime

from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class InMemoryRepository:
    """RepositoryPort implementation backed by in-memory dicts and lists."""

    def __init__(self) -> None:
        self._signals: list[Signal] = []
        self._decisions: list[RiskDecision] = []
        self._trades: list[Trade] = []
        self._positions: dict[str, Position] = {}

    def save_signal(self, signal: Signal) -> None:
        self._signals.append(signal)

    def save_decision(self, decision: RiskDecision) -> None:
        self._decisions.append(decision)

    def save_trade(self, trade: Trade) -> None:
        self._trades.append(trade)

    def save_position(self, position: Position) -> None:
        self._positions[position.symbol] = position

    def get_trades_today(self, symbol: str) -> list[Trade]:
        today = datetime.now(UTC).date()
        return [
            t
            for t in self._trades
            if t.symbol == symbol and t.timestamp.date() == today
        ]

    def get_current_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)
