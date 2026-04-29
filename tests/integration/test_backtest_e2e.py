"""Integration test — full SMA backtest on synthetic data, deterministic."""

from datetime import UTC, datetime, timedelta

import pytest

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover


def _make_trending_bars(count: int) -> list[BarData]:
    """Generate bars with a clear uptrend followed by downtrend.

    This ensures the SMA crossover strategy will generate signals.
    """
    bars = []
    start = datetime(2025, 1, 1, tzinfo=UTC)

    for i in range(count):
        # First half: uptrend, second half: downtrend
        if i < count // 2:
            price = 100.0 + i * 0.5
        else:
            price = 100.0 + (count // 2) * 0.5 - (i - count // 2) * 0.5

        bars.append(
            BarData(
                timestamp=start + timedelta(minutes=i),
                open=price - 0.1,
                high=price + 0.2,
                low=price - 0.2,
                close=price,
                volume=1000.0,
                symbol="TEST",
            )
        )
    return bars


@pytest.mark.integration
class TestBacktestEndToEnd:
    def test_sma_crossover_on_trending_data(self) -> None:
        """Full backtest with real SMA strategy on synthetic trending data."""
        bars = _make_trending_bars(200)
        config = BacktestConfig(
            symbol="TEST",
            initial_capital=100_000.0,
            position_size=1.0,
            spread_pips=0.1,
            slippage_pips=0.05,
            commission_per_trade=0.0,
            deterministic_seed=42,
            bar_count=35,
        )

        strategy = SMACrossover(short_window=10, long_window=30)
        risk_engine = RiskEngine(
            max_position_size=10,
            max_daily_loss=5000.0,
            max_trades_per_day=100,
        )
        market_data = HistoricalMarketDataAdapter(bars, bar_count=35)
        broker = SimulatedBrokerAdapter(
            initial_capital=config.initial_capital,
            spread=config.spread_pips,
            slippage=config.slippage_pips,
            commission_per_trade=config.commission_per_trade,
            seed=config.deterministic_seed,
        )
        repository = InMemoryRepository()

        runner = BacktestRunner(
            strategy=strategy,
            risk_engine=risk_engine,
            market_data=market_data,
            broker=broker,
            repository=repository,
            config=config,
        )

        result = runner.run()

        # Verify structure
        assert result.strategy_name == "sma_crossover"
        assert result.symbol == "TEST"
        assert len(result.trades) > 0  # Orders were executed
        assert result.metrics.initial_capital == 100_000.0
        assert len(result.equity_curve) > 0

        # Verify determinism
        market_data2 = HistoricalMarketDataAdapter(bars, bar_count=35)
        broker2 = SimulatedBrokerAdapter(
            initial_capital=config.initial_capital,
            spread=config.spread_pips,
            slippage=config.slippage_pips,
            commission_per_trade=config.commission_per_trade,
            seed=config.deterministic_seed,
        )
        repository2 = InMemoryRepository()
        runner2 = BacktestRunner(
            strategy=strategy,
            risk_engine=risk_engine,
            market_data=market_data2,
            broker=broker2,
            repository=repository2,
            config=config,
        )
        result2 = runner2.run()

        assert result.metrics.final_capital == result2.metrics.final_capital
        assert result.metrics.trade_count == result2.metrics.trade_count
        assert result.equity_curve == result2.equity_curve
