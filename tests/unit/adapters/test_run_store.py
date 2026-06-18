"""Unit tests for SQLiteRunStore — the durable per-run history rollup.

Covers the start_run → finish_run round-trip, multi-tenant isolation, the crash
case (start with no finish stays 'running'), and list ordering.
"""

from datetime import UTC, datetime
from pathlib import Path

from aurex_trade.adapters.sqlite.run_store import SQLiteRunStore

_USER = "user-a"
_OTHER = "user-b"


def _store(tmp_path: Path) -> SQLiteRunStore:
    return SQLiteRunStore(db_path=tmp_path / "test.db")


def _start(store: SQLiteRunStore, run_id: str, *, user_id: str = _USER, when: str) -> None:
    store.start_run(
        run_id,
        user_id=user_id,
        strategy="ciby_sliding_grid",
        symbol="XAU_USD",
        interval=60,
        strategy_params={"grid_spacing": 15},
        risk_params={"enabled": False},
        started_at=datetime.fromisoformat(when),
        initial_equity=10_000.0,
    )


def test_start_then_finish_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _start(store, "run1", when="2026-06-18T10:00:00+00:00")

    running = store.get_run("run1", user_id=_USER)
    assert running is not None
    assert running["status"] == "running"
    assert running["strategy"] == "ciby_sliding_grid"
    assert running["strategy_params"] == {"grid_spacing": 15}
    assert running["ended_at"] is None
    assert running["net_realized_pnl"] is None

    store.finish_run(
        "run1",
        user_id=_USER,
        ended_at=datetime(2026, 6, 18, 10, 30, tzinfo=UTC),
        stop_reason="stopped",
        total_cycles=30,
        sessions=2,
        closures=5,
        net_realized_pnl=24.5,
        final_equity=10_024.5,
    )

    done = store.get_run("run1", user_id=_USER)
    assert done is not None
    assert done["status"] == "stopped"
    assert done["stop_reason"] == "stopped"
    assert done["total_cycles"] == 30
    assert done["sessions"] == 2
    assert done["closures"] == 5
    assert done["net_realized_pnl"] == 24.5
    assert done["final_equity"] == 10_024.5
    assert done["ended_at"] is not None


def test_crash_case_run_stays_running(tmp_path: Path) -> None:
    """A run with no finish_run (crash/kill) remains 'running' — itself diagnostic."""
    store = _store(tmp_path)
    _start(store, "run1", when="2026-06-18T10:00:00+00:00")

    rec = store.get_run("run1", user_id=_USER)
    assert rec is not None
    assert rec["status"] == "running"
    assert rec["ended_at"] is None


def test_user_isolation(tmp_path: Path) -> None:
    """A user only ever sees their own runs; finish_run can't cross tenants."""
    store = _store(tmp_path)
    _start(store, "run-a", user_id=_USER, when="2026-06-18T10:00:00+00:00")
    _start(store, "run-b", user_id=_OTHER, when="2026-06-18T11:00:00+00:00")

    assert store.get_run("run-b", user_id=_USER) is None
    assert [r["run_id"] for r in store.list_runs(user_id=_USER)] == ["run-a"]
    assert [r["run_id"] for r in store.list_runs(user_id=_OTHER)] == ["run-b"]

    # Attempting to finish another user's run is a no-op (WHERE user_id guards it).
    store.finish_run(
        "run-b",
        user_id=_USER,
        ended_at=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        stop_reason="stopped",
        total_cycles=1,
        sessions=1,
        closures=0,
        net_realized_pnl=0.0,
        final_equity=10_000.0,
    )
    other = store.get_run("run-b", user_id=_OTHER)
    assert other is not None
    assert other["status"] == "running"  # untouched


def test_list_runs_ordered_most_recent_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _start(store, "old", when="2026-06-18T09:00:00+00:00")
    _start(store, "new", when="2026-06-18T12:00:00+00:00")
    _start(store, "mid", when="2026-06-18T10:30:00+00:00")

    assert [r["run_id"] for r in store.list_runs(user_id=_USER)] == ["new", "mid", "old"]
