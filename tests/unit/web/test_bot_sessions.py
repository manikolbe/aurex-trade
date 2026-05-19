"""Tests for BotSessionManager — per-user bot session lifecycle."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from aurex_trade.web._bot_sessions import (
    ActiveBotSession,
    BotAlreadyRunningError,
    BotSessionManager,
)


class FakeEngine:
    """Minimal stand-in for TradingEngine with a recordable stop()."""

    def __init__(self, *, raise_on_stop: bool = False) -> None:
        self.stop_called = False
        self._raise_on_stop = raise_on_stop

    def stop(self) -> None:
        self.stop_called = True
        if self._raise_on_stop:
            raise RuntimeError("engine stop failed")


class FakeConnection:
    """Minimal stand-in satisfying the Disconnectable protocol."""

    def __init__(self, *, raise_on_disconnect: bool = False) -> None:
        self.disconnect_called = False
        self._raise_on_disconnect = raise_on_disconnect

    def disconnect(self) -> None:
        self.disconnect_called = True
        if self._raise_on_disconnect:
            raise RuntimeError("disconnect failed")


def _make_manager() -> BotSessionManager:
    return BotSessionManager()


def _start_session(
    manager: BotSessionManager,
    user_id: str = "user-1",
    engine: FakeEngine | None = None,
    connection: FakeConnection | None = None,
) -> ActiveBotSession:
    return manager.start(
        user_id=user_id,
        engine=engine or FakeEngine(),  # type: ignore[arg-type]
        connection=connection or FakeConnection(),
        symbol="XAU_USD",
        strategy_name="sma_crossover",
    )


class TestStart:
    """Starting a bot session."""

    def test_start_registers_session(self) -> None:
        manager = _make_manager()
        session = _start_session(manager)

        assert isinstance(session, ActiveBotSession)
        assert session.user_id == "user-1"
        assert session.symbol == "XAU_USD"
        assert session.strategy_name == "sma_crossover"

    def test_start_records_utc_timestamp(self) -> None:
        manager = _make_manager()
        before = datetime.now(UTC)
        session = _start_session(manager)
        after = datetime.now(UTC)

        assert session.started_at.tzinfo is not None
        assert before <= session.started_at <= after

    def test_start_rejects_duplicate(self) -> None:
        manager = _make_manager()
        _start_session(manager, user_id="user-1")

        with pytest.raises(BotAlreadyRunningError) as exc_info:
            _start_session(manager, user_id="user-1")

        assert exc_info.value.user_id == "user-1"

    def test_start_allows_different_users(self) -> None:
        manager = _make_manager()
        _start_session(manager, user_id="user-1")
        _start_session(manager, user_id="user-2")

        assert manager.is_running("user-1")
        assert manager.is_running("user-2")


class TestStop:
    """Stopping a bot session."""

    def test_stop_calls_engine_stop(self) -> None:
        manager = _make_manager()
        engine = FakeEngine()
        _start_session(manager, engine=engine)

        manager.stop("user-1")
        assert engine.stop_called

    def test_stop_calls_connection_disconnect(self) -> None:
        manager = _make_manager()
        conn = FakeConnection()
        _start_session(manager, connection=conn)

        manager.stop("user-1")
        assert conn.disconnect_called

    def test_stop_removes_session(self) -> None:
        manager = _make_manager()
        _start_session(manager)

        manager.stop("user-1")
        assert manager.get("user-1") is None

    def test_stop_idempotent_on_missing(self) -> None:
        manager = _make_manager()
        manager.stop("nonexistent")  # should not raise

    def test_stop_engine_error_still_disconnects(self) -> None:
        manager = _make_manager()
        engine = FakeEngine(raise_on_stop=True)
        conn = FakeConnection()
        _start_session(manager, engine=engine, connection=conn)

        manager.stop("user-1")
        assert engine.stop_called
        assert conn.disconnect_called

    def test_stop_connection_error_does_not_raise(self) -> None:
        manager = _make_manager()
        conn = FakeConnection(raise_on_disconnect=True)
        _start_session(manager, connection=conn)

        manager.stop("user-1")  # should not raise
        assert conn.disconnect_called

    def test_stop_removes_session_even_on_errors(self) -> None:
        manager = _make_manager()
        engine = FakeEngine(raise_on_stop=True)
        conn = FakeConnection(raise_on_disconnect=True)
        _start_session(manager, engine=engine, connection=conn)

        manager.stop("user-1")
        assert manager.get("user-1") is None


class TestGet:
    """Retrieving a bot session."""

    def test_get_returns_session(self) -> None:
        manager = _make_manager()
        session = _start_session(manager)

        assert manager.get("user-1") is session

    def test_get_returns_none_for_unknown(self) -> None:
        manager = _make_manager()
        assert manager.get("unknown") is None


class TestIsRunning:
    """Checking whether a user's bot is running."""

    def test_is_running_true(self) -> None:
        manager = _make_manager()
        _start_session(manager)

        assert manager.is_running("user-1") is True

    def test_is_running_false(self) -> None:
        manager = _make_manager()
        assert manager.is_running("unknown") is False

    def test_is_running_false_after_stop(self) -> None:
        manager = _make_manager()
        _start_session(manager)

        manager.stop("user-1")
        assert manager.is_running("user-1") is False


class TestConcurrency:
    """Thread-safety of the session manager."""

    def test_concurrent_start_and_stop_different_users(self) -> None:
        manager = _make_manager()
        errors: list[Exception] = []

        def worker(uid: str) -> None:
            try:
                manager.start(
                    user_id=uid,
                    engine=FakeEngine(),  # type: ignore[arg-type]
                    connection=FakeConnection(),
                    symbol="XAU_USD",
                    strategy_name="sma_crossover",
                )
                manager.stop(uid)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"user-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_start_same_user(self) -> None:
        manager = _make_manager()
        successes: list[str] = []
        failures: list[str] = []

        def worker() -> None:
            try:
                manager.start(
                    user_id="user-race",
                    engine=FakeEngine(),  # type: ignore[arg-type]
                    connection=FakeConnection(),
                    symbol="XAU_USD",
                    strategy_name="sma_crossover",
                )
                successes.append("ok")
            except BotAlreadyRunningError:
                failures.append("rejected")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 1
        assert len(failures) == 9
