"""Unit tests: run/session identity bound onto every log line via contextvars.

Verifies that run() mints a run_id and binds user_id/run_id/strategy onto the log
context (so every line carries them), that session_seq advances on each grid birth,
that the context is cleared on exit, and that a reused thread can't leak one run's
identity into the next. Also checks the durable run-history rollup is driven.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import structlog

from aurex_trade.domain.models import BarData
from aurex_trade.engine.trading_engine import TradingEngine


@pytest.fixture(autouse=True)
def _restore_structlog() -> Iterator[None]:
    """Restore structlog's global config + clear contextvars after each test.

    These tests reconfigure structlog to capture events; isolate that from the
    rest of the suite.
    """
    try:
        yield
    finally:
        structlog.contextvars.clear_contextvars()
        structlog.reset_defaults()


def _configure_capture() -> list[dict[str, object]]:
    """Configure structlog so emitted events (with contextvars merged in) are captured."""
    captured: list[dict[str, object]] = []

    def sink(_logger: object, _name: str, event_dict: dict[str, object]) -> str:
        captured.append(dict(event_dict))
        return ""

    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, sink],
        cache_logger_on_first_use=False,
    )
    return captured


def _events(captured: list[dict[str, object]], name: str) -> list[dict[str, object]]:
    return [e for e in captured if e.get("event") == name]


def _make_engine(
    *, user_id: str = "u1", run_store: MagicMock | None = None
) -> TradingEngine:
    broker = MagicMock()
    broker.equity = 10_000.0
    broker.get_open_trades.return_value = []
    broker.get_positions.return_value = None
    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    return TradingEngine(
        strategy=strategy,
        risk_engine=MagicMock(),
        broker=broker,
        market_data=MagicMock(),
        repository=MagicMock(),
        symbol="XAU_USD",
        interval_seconds=0,
        bar_count=10,
        user_id=user_id,
        run_store=run_store,
    )


def test_engine_started_carries_run_id_and_context() -> None:
    captured = _configure_capture()
    engine = _make_engine()

    engine.run(max_cycles=0)

    started = _events(captured, "engine_started")
    assert len(started) == 1
    ev = started[0]
    # run_id is on the payload AND bound into the context (merged onto the line).
    assert ev["run_id"]
    assert ev["user_id"] == "u1"
    assert ev["strategy"] == "ciby_sliding_grid"
    # engine_stopped also carries the bound identity.
    stopped = _events(captured, "engine_stopped")
    assert stopped and stopped[0]["run_id"] == ev["run_id"]


def test_context_cleared_after_run() -> None:
    _configure_capture()
    engine = _make_engine()
    engine.run(max_cycles=0)
    # No bound context should survive the run on this thread.
    assert structlog.contextvars.get_contextvars() == {}


def test_no_identity_leak_across_sequential_runs() -> None:
    """A reused thread must not carry run A's identity into run B."""
    captured = _configure_capture()

    engine_a = _make_engine(user_id="user-a")
    engine_a.run(max_cycles=0)
    run_a_id = _events(captured, "engine_started")[0]["run_id"]

    captured.clear()
    engine_b = _make_engine(user_id="user-b")
    engine_b.run(max_cycles=0)
    b_started = _events(captured, "engine_started")[0]

    assert b_started["user_id"] == "user-b"
    assert b_started["run_id"] != run_a_id


def test_session_seq_increments_on_each_grid_birth() -> None:
    captured = _configure_capture()
    engine = _make_engine()

    # Bars for the cycle.
    bars = [
        BarData(
            timestamp=datetime(2026, 6, 18, 10, 0, tzinfo=UTC),
            open=2300.0, high=2305.0, low=2295.0, close=2300.0,
            volume=1.0, symbol="XAU_USD",
        )
    ]
    engine._market_data.get_latest_bars.return_value = bars
    # No signal so order processing is skipped; no update_unrealized_pnl attr.
    engine._strategy.generate.return_value = None
    del engine._strategy.update_unrealized_pnl
    engine._strategy.get_display_state.return_value = {
        "anchor_price": 2300.0,
        "grid_levels": [],
    }

    # Bind a base context as run() would, then drive cycles directly.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(user_id="u1", run_id="r1", strategy="s")

    # First grid birth → session_seq 1.
    engine._run_strategy_cycle()
    assert engine._session_seq == 1
    assert _events(captured, "grid_initialized")[-1]["session_seq"] == 1

    # Simulate a re-anchor: close-all resets _grid_logged.
    engine._grid_logged = False
    engine._run_strategy_cycle()
    assert engine._session_seq == 2
    assert _events(captured, "grid_initialized")[-1]["session_seq"] == 2

    structlog.contextvars.clear_contextvars()


def test_run_store_start_and_finish_called() -> None:
    _configure_capture()
    run_store = MagicMock()
    engine = _make_engine(run_store=run_store)

    engine.run(max_cycles=0)

    run_store.start_run.assert_called_once()
    run_store.finish_run.assert_called_once()
    start_kwargs = run_store.start_run.call_args.kwargs
    finish_kwargs = run_store.finish_run.call_args.kwargs
    assert start_kwargs["user_id"] == "u1"
    assert start_kwargs["strategy"] == "ciby_sliding_grid"
    assert finish_kwargs["stop_reason"] == "max_cycles"
    # Same run_id flows from start to finish (positional run_id arg).
    assert run_store.start_run.call_args.args[0] == run_store.finish_run.call_args.args[0]
