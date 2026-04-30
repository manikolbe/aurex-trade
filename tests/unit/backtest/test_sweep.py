"""Tests for ParameterSweep — grid generation, filtering, ranking, determinism."""

from datetime import UTC, datetime, timedelta

from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.sweep import ParameterSweep
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover


def _make_trending_bars(count: int) -> list[BarData]:
    """Generate bars with a clear uptrend followed by downtrend."""
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


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbol="TEST",
        initial_capital=100_000.0,
        position_size=1.0,
        spread_pips=0.0,
        slippage_pips=0.0,
        commission_per_trade=0.0,
        deterministic_seed=42,
    )


def _risk_engine() -> RiskEngine:
    return RiskEngine(
        max_position_size=100,
        max_daily_loss=50_000.0,
        max_trades_per_day=1000,
    )


def _sma_factory(params: dict[str, int]) -> SMACrossover:
    return SMACrossover(
        short_window=params["short_window"], long_window=params["long_window"]
    )


class TestParameterSweep:
    def test_generates_all_valid_combinations(self) -> None:
        """Grid of 2x2 with no filtering = 4 results."""
        bars = _make_trending_bars(200)

        sweep = ParameterSweep(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
        )
        result = sweep.run()

        assert result.total_combinations == 4
        assert len(result.results) == 4

    def test_filters_invalid_combinations(self) -> None:
        """Validator removes combos where short >= long."""
        bars = _make_trending_bars(200)

        sweep = ParameterSweep(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10, 30], "long_window": [10, 20]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            param_validator=lambda p: p["short_window"] < p["long_window"],
        )
        result = sweep.run()

        # Valid: (5,10), (5,20), (10,20). Invalid: (10,10), (30,10), (30,20)
        assert result.total_combinations == 3
        assert len(result.results) == 3

    def test_results_ranked_by_metric(self) -> None:
        """Results are sorted descending by rank_by metric."""
        bars = _make_trending_bars(200)

        sweep = ParameterSweep(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            rank_by="sharpe_ratio",
        )
        result = sweep.run()

        sharpes = [r.metrics.sharpe_ratio for r in result.results]
        assert sharpes == sorted(sharpes, reverse=True)

    def test_results_ranked_by_pnl(self) -> None:
        """Can rank by total_pnl instead."""
        bars = _make_trending_bars(200)

        sweep = ParameterSweep(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            rank_by="total_pnl",
        )
        result = sweep.run()

        pnls = [r.metrics.total_pnl for r in result.results]
        assert pnls == sorted(pnls, reverse=True)
        assert result.rank_metric == "total_pnl"

    def test_deterministic_results(self) -> None:
        """Same inputs produce identical results."""
        bars = _make_trending_bars(200)

        def run_sweep() -> list[float]:
            sweep = ParameterSweep(
                strategy_factory=_sma_factory,
                param_grid={"short_window": [5, 10], "long_window": [20, 30]},
                bars=bars,
                config=_config(),
                risk_engine=_risk_engine(),
            )
            return [r.metrics.final_capital for r in sweep.run().results]

        assert run_sweep() == run_sweep()

    def test_parameters_attached_to_results(self) -> None:
        """Each result has its parameter combination attached."""
        bars = _make_trending_bars(200)

        sweep = ParameterSweep(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5], "long_window": [20]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
        )
        result = sweep.run()

        assert len(result.results) == 1
        assert result.results[0].parameters == {
            "short_window": "5",
            "long_window": "20",
        }
