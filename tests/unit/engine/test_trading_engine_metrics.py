"""Unit tests for TradingEngine observability metrics (issue #55)."""

from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.adapters.paper.broker import PaperBrokerAdapter
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _build_engine(
    seed: int = 42,
    kill_switch: bool = False,
) -> TradingEngine:
    """Build a TradingEngine with paper adapters for unit testing."""
    broker = PaperBrokerAdapter(base_price=180.0, seed=seed)
    repository = InMemoryRepository()
    strategy = SMACrossover(short_window=3, long_window=5, atr_period=3)
    risk_engine = RiskEngine(
        max_position_size=10,
        max_daily_loss=500.0,
        max_trades_per_day=10,
        kill_switch=kill_switch,
    )
    return TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=broker,
        repository=repository,
        symbol="GLD",
        interval_seconds=0,
        bar_count=10,
        user_id=_TEST_USER_ID,
    )


class TestGetMetricsInitialState:
    """Metrics before the engine has ever run."""

    def test_cycle_count_is_zero(self) -> None:
        engine = _build_engine()
        metrics = engine.get_metrics()
        assert metrics["cycle_count"] == 0

    def test_started_at_is_none(self) -> None:
        engine = _build_engine()
        metrics = engine.get_metrics()
        assert metrics["started_at"] is None

    def test_running_is_false(self) -> None:
        engine = _build_engine()
        metrics = engine.get_metrics()
        assert metrics["running"] is False

    def test_uptime_is_none(self) -> None:
        engine = _build_engine()
        metrics = engine.get_metrics()
        assert metrics["uptime_seconds"] is None

    def test_session_counters_are_zero(self) -> None:
        engine = _build_engine()
        metrics = engine.get_metrics()
        assert metrics["session_signals"] == 0
        assert metrics["session_trades"] == 0
        assert metrics["session_rejections"] == 0

    def test_peak_equity_is_zero(self) -> None:
        engine = _build_engine()
        metrics = engine.get_metrics()
        assert metrics["peak_equity"] == 0.0


class TestGetMetricsAfterRun:
    """Metrics after the engine has completed cycles."""

    def test_cycle_count_matches_max_cycles(self) -> None:
        engine = _build_engine()
        engine.run(max_cycles=5)
        metrics = engine.get_metrics()
        assert metrics["cycle_count"] == 5

    def test_cycle_count_ten_cycles(self) -> None:
        engine = _build_engine()
        engine.run(max_cycles=10)
        metrics = engine.get_metrics()
        assert metrics["cycle_count"] == 10

    def test_running_is_false_after_completion(self) -> None:
        engine = _build_engine()
        engine.run(max_cycles=3)
        metrics = engine.get_metrics()
        assert metrics["running"] is False

    def test_started_at_reset_after_stop(self) -> None:
        engine = _build_engine()
        engine.run(max_cycles=3)
        metrics = engine.get_metrics()
        assert metrics["started_at"] is None

    def test_uptime_is_none_after_stop(self) -> None:
        engine = _build_engine()
        engine.run(max_cycles=3)
        metrics = engine.get_metrics()
        assert metrics["uptime_seconds"] is None

    def test_peak_equity_set(self) -> None:
        engine = _build_engine()
        engine.run(max_cycles=1)
        metrics = engine.get_metrics()
        assert metrics["peak_equity"] > 0.0


class TestGetMetricsKillSwitch:
    """Metrics with kill switch enabled (all trades rejected)."""

    def test_rejections_counted_with_kill_switch(self) -> None:
        engine = _build_engine(kill_switch=True, seed=42)
        engine.run(max_cycles=20)
        metrics = engine.get_metrics()
        # If signals were generated, they should all be rejected
        if metrics["session_signals"] > 0:
            assert metrics["session_rejections"] == metrics["session_signals"]
        assert metrics["session_trades"] == 0
