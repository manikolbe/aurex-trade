"""SQLite run store — durable per-run history rollup.

Implements RunStorePort using Python's built-in sqlite3 module. Shares schema.sql
with SQLiteRepository (same database file). Stores one summary row per engine run;
see ports/run_store.py for why this is a summary, not an event log.
"""

import json
import sqlite3
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import TypedDict


class RunRecord(TypedDict):
    """A persisted run summary, as returned by list_runs/get_run."""

    run_id: str
    user_id: str
    strategy: str
    symbol: str
    interval: int
    strategy_params: dict[str, int | float]
    risk_params: dict[str, int | float | bool]
    started_at: str
    ended_at: str | None
    status: str
    stop_reason: str | None
    total_cycles: int | None
    sessions: int | None
    closures: int | None
    net_realized_pnl: float | None
    initial_equity: float | None
    final_equity: float | None


class SQLiteRunStore:
    """RunStorePort implementation backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: bot runs on a ThreadPoolExecutor worker, distinct
        # from the thread that constructs the store. WAL mode handles concurrency and
        # each write below is a single atomic, committed statement.
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

    def start_run(
        self,
        run_id: str,
        *,
        user_id: str,
        strategy: str,
        symbol: str,
        interval: int,
        strategy_params: dict[str, int | float],
        risk_params: dict[str, int | float | bool],
        started_at: datetime,
        initial_equity: float,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO bot_runs (
                run_id, user_id, strategy, symbol, interval,
                strategy_params, risk_params, started_at, status, initial_equity
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
            ON CONFLICT(run_id) DO NOTHING
            """,
            (
                run_id,
                user_id,
                strategy,
                symbol,
                interval,
                json.dumps(strategy_params),
                json.dumps(risk_params),
                started_at.isoformat(),
                initial_equity,
            ),
        )
        self._conn.commit()

    def finish_run(
        self,
        run_id: str,
        *,
        user_id: str,
        ended_at: datetime,
        stop_reason: str,
        total_cycles: int,
        sessions: int,
        closures: int,
        net_realized_pnl: float,
        final_equity: float,
    ) -> None:
        # user_id in the WHERE clause enforces multi-tenant isolation: a run can only
        # be finished by its owner.
        self._conn.execute(
            """
            UPDATE bot_runs SET
                ended_at = ?,
                status = 'stopped',
                stop_reason = ?,
                total_cycles = ?,
                sessions = ?,
                closures = ?,
                net_realized_pnl = ?,
                final_equity = ?
            WHERE run_id = ? AND user_id = ?
            """,
            (
                ended_at.isoformat(),
                stop_reason,
                total_cycles,
                sessions,
                closures,
                net_realized_pnl,
                final_equity,
                run_id,
                user_id,
            ),
        )
        self._conn.commit()

    def list_runs(self, *, user_id: str) -> list[RunRecord]:
        """Return the user's runs, most recent first."""
        cursor = self._conn.execute(
            "SELECT * FROM bot_runs WHERE user_id = ? ORDER BY started_at DESC",
            (user_id,),
        )
        return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_run(self, run_id: str, *, user_id: str) -> RunRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM bot_runs WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        )
        row = cursor.fetchone()
        return self._row_to_record(row) if row is not None else None

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            user_id=row["user_id"],
            strategy=row["strategy"],
            symbol=row["symbol"],
            interval=row["interval"],
            strategy_params=json.loads(row["strategy_params"]),
            risk_params=json.loads(row["risk_params"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            stop_reason=row["stop_reason"],
            total_cycles=row["total_cycles"],
            sessions=row["sessions"],
            closures=row["closures"],
            net_realized_pnl=row["net_realized_pnl"],
            initial_equity=row["initial_equity"],
            final_equity=row["final_equity"],
        )
