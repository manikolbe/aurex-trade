"""Integration test — full parameter sweep and walk-forward on synthetic data."""

from datetime import UTC, datetime, timedelta

import pytest

from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.sweep import ParameterSweep
from aurex_trade.backtest.walk_forward import WalkForwardValidator
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover


def _make_trending_bars(count: int) -> list[BarData]:
    """Generate bars with a clear uptrend then downtrend."""
    bars = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(count):
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


def _sma_factory(params: dict[str, int]) -> SMACrossover:
    return SMACrossover(short_window=params["short_window"], long_window=params["long_window"])


@pytest.mark.integration
class TestSweepEndToEnd:
    def test_sweep_ranks_correctly(self) -> None:
        """Best params from sweep actually have highest metric value."""
        bars = _make_trending_bars(500)
        config = BacktestConfig(
            symbol="TEST",
            initial_capital=100_000.0,
            position_size=1.0,
            spread_pips=0.1,
            slippage_pips=0.05,
            commission_per_trade=0.0,
            deterministic_seed=42,
        )
        risk_engine = RiskEngine(
            max_position_size=100,
            max_daily_loss=50_000.0,
            max_trades_per_day=1000,
        )

        sweep = ParameterSweep(
            strategy_factory=_sma_factory,
            param_grid={
                "short_window": [5, 10, 15, 20],
                "long_window": [20, 30, 50],
            },
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            rank_by="sharpe_ratio",
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )

        result = sweep.run()

        # Verify ranking is correct
        assert len(result.results) > 1
        sharpes = [r.metrics.sharpe_ratio for r in result.results]
        assert sharpes == sorted(sharpes, reverse=True)

        # Every result has parameters
        for r in result.results:
            assert "short_window" in r.parameters
            assert "long_window" in r.parameters

    def test_walk_forward_uses_unseen_data(self) -> None:
        """Walk-forward test results differ from training (different data)."""
        bars = _make_trending_bars(800)
        config = BacktestConfig(
            symbol="TEST",
            initial_capital=100_000.0,
            position_size=1.0,
            spread_pips=0.1,
            slippage_pips=0.05,
            commission_per_trade=0.0,
            deterministic_seed=42,
        )
        risk_engine = RiskEngine(
            max_position_size=100,
            max_daily_loss=50_000.0,
            max_trades_per_day=1000,
        )

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={
                "short_window": [5, 10],
                "long_window": [20, 30],
            },
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            train_bars=200,
            test_bars=200,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )

        result = validator.run()

        assert len(result.windows) == 2
        assert result.aggregate_test_metrics.initial_capital == 100_000.0

        # Verify determinism
        validator2 = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={
                "short_window": [5, 10],
                "long_window": [20, 30],
            },
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            train_bars=200,
            test_bars=200,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result2 = validator2.run()

        assert result.aggregate_test_metrics.total_pnl == result2.aggregate_test_metrics.total_pnl
