"""In-memory repository — stores all trading data in plain Python collections.

Used for local mode and integration tests. No external dependencies.
Data does not survive process restarts — use SQLite adapter for persistence.
"""

from datetime import UTC, datetime

from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class InMemoryRepository:
    """RepositoryPort implementation backed by in-memory dicts and lists."""

    def __init__(self) -> None:
        self._signals: list[tuple[str, Signal]] = []
        self._decisions: list[tuple[str, RiskDecision]] = []
        self._trades: list[tuple[str, Trade]] = []
        self._positions: dict[tuple[str, str], Position] = {}

    @property
    def signal_count(self) -> int:
        """Total number of stored signals (all users)."""
        return len(self._signals)

    @property
    def decision_count(self) -> int:
        """Total number of stored decisions (all users)."""
        return len(self._decisions)

    @property
    def trade_count(self) -> int:
        """Total number of stored trades (all users)."""
        return len(self._trades)

    def save_signal(self, signal: Signal, *, user_id: str) -> None:
        self._signals.append((user_id, signal))

    def save_decision(self, decision: RiskDecision, *, user_id: str) -> None:
        self._decisions.append((user_id, decision))

    def save_trade(self, trade: Trade, *, user_id: str) -> None:
        self._trades.append((user_id, trade))

    def save_position(self, position: Position, *, user_id: str) -> None:
        self._positions[(user_id, position.symbol)] = position

    def get_trades_today(self, symbol: str, *, user_id: str) -> list[Trade]:
        today = datetime.now(UTC).date()
        return [
            t
            for uid, t in self._trades
            if uid == user_id and t.symbol == symbol and t.timestamp.date() == today
        ]

    def get_current_position(self, symbol: str, *, user_id: str) -> Position | None:
        return self._positions.get((user_id, symbol))
