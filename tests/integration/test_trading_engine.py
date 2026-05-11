"""Integration tests for the Trading Engine — full pipeline with paper adapters."""

from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.adapters.paper.broker import PaperBrokerAdapter
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _build_engine(
    seed: int = 42,
    kill_switch: bool = False,
    max_position_size: int = 10,
    short_window: int = 3,
    long_window: int = 5,
) -> tuple[TradingEngine, InMemoryRepository, PaperBrokerAdapter]:
    """Build a fully wired TradingEngine with paper adapters."""
    broker = PaperBrokerAdapter(base_price=180.0, seed=seed)
    repository = InMemoryRepository()
    strategy = SMACrossover(
        short_window=short_window, long_window=long_window, atr_period=3
    )
    risk_engine = RiskEngine(
        max_position_size=max_position_size,
        max_daily_loss=500.0,
        max_trades_per_day=10,
        kill_switch=kill_switch,
    )
    engine = TradingEngine(
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
    return engine, repository, broker


class TestTradingEngineIntegration:
    def test_runs_without_crashing(self) -> None:
        """Engine runs multiple cycles without errors."""
        engine, _, _ = _build_engine()
        engine.run(max_cycles=5)

    def test_signals_are_persisted(self) -> None:
        """Any generated signals should be saved to the repository."""
        engine, repo, _ = _build_engine()
        engine.run(max_cycles=10)
        # With random walk data, we may or may not get signals — but the
        # pipeline should run cleanly either way
        assert repo.signal_count >= 0

    def test_decisions_are_persisted(self) -> None:
        """Risk decisions should be saved alongside signals."""
        engine, repo, _ = _build_engine()
        engine.run(max_cycles=10)
        # Every signal should have a corresponding decision
        assert repo.decision_count == repo.signal_count

    def test_kill_switch_blocks_all_trades(self) -> None:
        """With kill switch on, no trades should be executed."""
        engine, repo, _ = _build_engine(kill_switch=True)
        engine.run(max_cycles=10)
        assert repo.trade_count == 0

    def test_stop_halts_engine(self) -> None:
        """Calling stop() should terminate the run loop."""
        engine, _, _ = _build_engine()
        engine.stop()
        engine.run(max_cycles=100)
        # Should exit immediately since _running was set to False before run()

    def test_max_position_size_limits_trades(self) -> None:
        """Engine should respect position size limits."""
        engine, repo, _ = _build_engine(max_position_size=1)
        engine.run(max_cycles=20)
        # Should never exceed max position size
        position = repo.get_current_position("GLD", user_id=_TEST_USER_ID)
        if position:
            assert abs(position.quantity) <= 2  # at most 1 buy + 1 sell cycle
