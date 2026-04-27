"""SQLite repository — persistent storage for trading data.

Implements RepositoryPort using Python's built-in sqlite3 module.
Schema is auto-created on first run. WAL mode enabled for safe concurrent reads.
All queries use parameterized statements — no string interpolation in SQL.
"""

import json
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from uuid import UUID

from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class SQLiteRepository:
    """RepositoryPort implementation backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()

    def _apply_schema(self) -> None:
        schema_sql = (
            resources.files("aurex_trade.adapters.sqlite")
            .joinpath("schema.sql")
            .read_text(encoding="utf-8")
        )
        self._conn.executescript(schema_sql)

    def close(self) -> None:
        self._conn.close()

    # -- Saves --

    def save_signal(self, signal: Signal) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO signals
                (id, timestamp, symbol, signal_type, strategy_name, strength, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(signal.id),
                signal.timestamp.isoformat(),
                signal.symbol,
                signal.signal_type.value,
                signal.strategy_name,
                signal.strength,
                json.dumps(signal.metadata),
            ),
        )
        self._conn.commit()

    def save_decision(self, decision: RiskDecision) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO decisions
                (signal_id, action, reason, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (
                str(decision.signal_id),
                decision.action.value,
                decision.reason,
                decision.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    def save_trade(self, trade: Trade) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO trades
                (id, order_id, symbol, side, quantity, price, commission, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(trade.id),
                str(trade.order_id),
                trade.symbol,
                trade.side.value,
                trade.quantity,
                trade.price,
                trade.commission,
                trade.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    def save_position(self, position: Position) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO positions
                (symbol, quantity, average_cost, market_value,
                 unrealized_pnl, realized_pnl, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.symbol,
                position.quantity,
                position.average_cost,
                position.market_value,
                position.unrealized_pnl,
                position.realized_pnl,
                position.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    # -- Queries --

    def get_trades_today(self, symbol: str) -> list[Trade]:
        today = datetime.now(UTC).date().isoformat()
        cursor = self._conn.execute(
            """
            SELECT id, order_id, symbol, side, quantity, price, commission, timestamp
            FROM trades
            WHERE symbol = ? AND timestamp >= ?
            ORDER BY timestamp
            """,
            (symbol, today),
        )
        return [self._row_to_trade(row) for row in cursor.fetchall()]

    def get_current_position(self, symbol: str) -> Position | None:
        cursor = self._conn.execute(
            "SELECT * FROM positions WHERE symbol = ?",
            (symbol,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_position(row)

    # -- Row mappers --

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> Trade:
        return Trade(
            id=UUID(row["id"]),
            order_id=UUID(row["order_id"]),
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            quantity=row["quantity"],
            price=row["price"],
            commission=row["commission"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> Position:
        return Position(
            symbol=row["symbol"],
            quantity=row["quantity"],
            average_cost=row["average_cost"],
            market_value=row["market_value"],
            unrealized_pnl=row["unrealized_pnl"],
            realized_pnl=row["realized_pnl"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )
