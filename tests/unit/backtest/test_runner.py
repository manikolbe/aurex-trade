"""Tests for the BacktestRunner — verify orchestration with trivial strategy."""

from datetime import UTC, datetime, timedelta

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.risk.engine import RiskEngine


def _make_bars(count: int, base_price: float = 100.0) -> list[BarData]:
    """Generate synthetic bars with a simple uptrend."""
    bars = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(count):
        price = base_price + i * 0.1
        bars.append(
            BarData(
                timestamp=start + timedelta(minutes=i),
                open=price,
                high=price + 0.05,
                low=price - 0.05,
                close=price,
                volume=1000.0,
                symbol="TEST",
            )
        )
    return bars


class AlwaysBuyStrategy:
    """Trivial strategy: always generates a LONG signal."""

    @property
    def name(self) -> str:
        return "always_buy"

    def generate(self, bars: list[BarData]) -> Signal | None:
        return Signal(
            symbol=bars[-1].symbol,
            signal_type=SignalType.LONG,
            strategy_name=self.name,
            strength=1.0,
        )


class NeverTradeStrategy:
    """Trivial strategy: never generates a signal."""

    @property
    def name(self) -> str:
        return "never_trade"

    def generate(self, bars: list[BarData]) -> Signal | None:
        return None


class TestBacktestRunner:
    def _config(self) -> BacktestConfig:
        return BacktestConfig(
            symbol="TEST",
            initial_capital=100_000.0,
            position_size=1.0,
            spread_pips=0.0,
            slippage_pips=0.0,
            commission_per_trade=0.0,
            deterministic_seed=42,
            bar_count=10,
        )

    def test_no_signal_strategy_produces_no_trades(self) -> None:
        bars = _make_bars(60)
        config = self._config()
        market_data = HistoricalMarketDataAdapter(bars, bar_count=10)
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        risk = RiskEngine(
            max_position_size=10, max_daily_loss=500.0, max_trades_per_day=100
        )
        repo = InMemoryRepository()

        runner = BacktestRunner(
            strategy=NeverTradeStrategy(),
            risk_engine=risk,
            market_data=market_data,
            broker=broker,
            repository=repo,
            config=config,
        )
        result = runner.run()

        assert result.metrics.trade_count == 0
        assert result.metrics.total_pnl == 0.0
        assert result.strategy_name == "never_trade"

    def test_always_buy_produces_trades(self) -> None:
        bars = _make_bars(60)
        config = self._config()
        market_data = HistoricalMarketDataAdapter(bars, bar_count=10)
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        risk = RiskEngine(
            max_position_size=100, max_daily_loss=50_000.0, max_trades_per_day=1000
        )
        repo = InMemoryRepository()

        runner = BacktestRunner(
            strategy=AlwaysBuyStrategy(),
            risk_engine=risk,
            market_data=market_data,
            broker=broker,
            repository=repo,
            config=config,
        )
        result = runner.run()

        # Should have executed orders (buy-only means no round trips completed)
        assert len(result.trades) > 0
        assert result.symbol == "TEST"
        # Buy-only strategy has no realized P&L, so trade_count (round trips) is 0
        assert result.metrics.trade_count == 0

    def test_deterministic_results(self) -> None:
        """Same seed and data should produce identical results."""
        bars = _make_bars(60)

        def run_once() -> float:
            config = BacktestConfig(
                symbol="TEST",
                initial_capital=100_000.0,
                position_size=1.0,
                spread_pips=0.5,
                slippage_pips=0.2,
                commission_per_trade=1.0,
                deterministic_seed=123,
                bar_count=10,
            )
            market_data = HistoricalMarketDataAdapter(bars, bar_count=10)
            broker = SimulatedBrokerAdapter(
                initial_capital=100_000.0, spread=0.5, slippage=0.2,
                commission_per_trade=1.0, seed=123
            )
            risk = RiskEngine(
                max_position_size=100, max_daily_loss=50_000.0, max_trades_per_day=1000
            )
            repo = InMemoryRepository()
            runner = BacktestRunner(
                strategy=AlwaysBuyStrategy(),
                risk_engine=risk,
                market_data=market_data,
                broker=broker,
                repository=repo,
                config=config,
            )
            return runner.run().metrics.final_capital

        result1 = run_once()
        result2 = run_once()
        assert result1 == result2

    def test_equity_curve_has_correct_length(self) -> None:
        bars = _make_bars(60)
        config = self._config()
        market_data = HistoricalMarketDataAdapter(bars, bar_count=10)
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        risk = RiskEngine(
            max_position_size=100, max_daily_loss=50_000.0, max_trades_per_day=1000
        )
        repo = InMemoryRepository()

        runner = BacktestRunner(
            strategy=NeverTradeStrategy(),
            risk_engine=risk,
            market_data=market_data,
            broker=broker,
            repository=repo,
            config=config,
        )
        result = runner.run()

        # Equity curve starts with initial capital + one entry per step
        total_steps = len(bars) - config.bar_count
        assert len(result.equity_curve) == total_steps + 1
