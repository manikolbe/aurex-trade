"""SQLite market data store — persistent storage for historical bars and user preferences.

Implements HistoricalDataPort using Python's built-in sqlite3 module.
Uses INSERT OR IGNORE for idempotent, concurrent-safe writes.
"""

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from aurex_trade.domain.models import BarData

_BATCH_SIZE = 1000


class SQLiteMarketDataStore:
    """HistoricalDataPort implementation backed by SQLite.

    Market data is shared across all users. WAL mode + INSERT OR IGNORE
    ensures safe concurrent writes from multiple threads/processes.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
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

    def save_bars(self, bars: list[BarData], symbol: str, granularity: str) -> None:
        """Persist bars using INSERT OR IGNORE. Duplicates are silently skipped."""
        if not bars:
            return

        for i in range(0, len(bars), _BATCH_SIZE):
            batch = bars[i : i + _BATCH_SIZE]
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO bars
                    (symbol, granularity, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        symbol,
                        granularity,
                        bar.timestamp.isoformat(),
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                    )
                    for bar in batch
                ],
            )
        self._conn.commit()

    def load_bars(
        self,
        symbol: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[BarData]:
        """Load bars, optionally filtered by date range. Returns sorted by timestamp."""
        query = "SELECT * FROM bars WHERE symbol = ? AND granularity = ?"
        params: list[str] = [symbol, granularity]

        if start is not None:
            query += " AND timestamp >= ?"
            params.append(start.isoformat())
        if end is not None:
            query += " AND timestamp <= ?"
            params.append(end.isoformat())

        query += " ORDER BY timestamp"

        cursor = self._conn.execute(query, params)
        bars: list[BarData] = []
        for row in cursor:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            bars.append(
                BarData(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    symbol=row["symbol"],
                )
            )
        return bars

    def get_date_range(
        self, symbol: str, granularity: str
    ) -> tuple[datetime, datetime] | None:
        """Return (min_timestamp, max_timestamp) for stored data, or None if empty."""
        cursor = self._conn.execute(
            "SELECT MIN(timestamp) AS min_ts, MAX(timestamp) AS max_ts "
            "FROM bars WHERE symbol = ? AND granularity = ?",
            (symbol, granularity),
        )
        row = cursor.fetchone()
        if row is None or row["min_ts"] is None:
            return None

        min_ts = datetime.fromisoformat(row["min_ts"])
        max_ts = datetime.fromisoformat(row["max_ts"])
        if min_ts.tzinfo is None:
            min_ts = min_ts.replace(tzinfo=UTC)
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=UTC)
        return (min_ts, max_ts)


class UserDataPreferencesStore:
    """Per-user date range preferences for backtest symbol/granularity pairs.

    Remembers the last-used date range so the UI can pre-fill it on next visit.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
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

    def save_preference(
        self,
        user_id: str,
        symbol: str,
        granularity: str,
        start_date: str,
        end_date: str,
    ) -> None:
        """Upsert the user's preferred date range for a symbol/granularity."""
        now = datetime.now(tz=UTC).isoformat()
        self._conn.execute(
            """
            INSERT INTO user_data_preferences
                (user_id, symbol, granularity, start_date, end_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, symbol, granularity) DO UPDATE SET
                start_date = excluded.start_date,
                end_date = excluded.end_date,
                updated_at = excluded.updated_at
            """,
            (user_id, symbol, granularity, start_date, end_date, now),
        )
        self._conn.commit()

    def get_preference(
        self, user_id: str, symbol: str, granularity: str
    ) -> tuple[str, str] | None:
        """Return (start_date, end_date) for the user's saved preference, or None."""
        cursor = self._conn.execute(
            """
            SELECT start_date, end_date FROM user_data_preferences
            WHERE user_id = ? AND symbol = ? AND granularity = ?
            """,
            (user_id, symbol, granularity),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return (row["start_date"], row["end_date"])
