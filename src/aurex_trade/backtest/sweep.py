"""Parameter sweep — grid search over strategy parameters.

Runs BacktestRunner for every valid parameter combination, ranks results
by a configurable metric. Deterministic via shared seed.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import structlog

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.results import BacktestResult, SweepResult
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy

log = structlog.get_logger()


class ParameterSweep:
    """Grid search over strategy parameters using BacktestRunner.

    Each combination gets a fresh broker/market_data/repository to ensure
    isolation. All runs use the same seed for fair comparison.
    """

    def __init__(
        self,
        strategy_factory: Callable[[dict[str, int | float]], Strategy],
        param_grid: dict[str, list[int | float]],
        bars: list[BarData],
        config: BacktestConfig,
        risk_engine: RiskEngine,
        rank_by: str = "sharpe_ratio",
        param_validator: Callable[[dict[str, int | float]], bool] | None = None,
        *,
        user_id: str,
    ) -> None:
        self._strategy_factory = strategy_factory
        self._param_grid = param_grid
        self._bars = bars
        self._config = config
        self._risk_engine = risk_engine
        self._rank_by = rank_by
        self._param_validator = param_validator
        self._user_id = user_id

    def run(self) -> SweepResult:
        """Run backtest for every valid parameter combination, return ranked."""
        combinations = self._generate_combinations()
        results: list[BacktestResult] = []

        log.info(
            "sweep_started",
            total_combinations=len(combinations),
            rank_by=self._rank_by,
        )

        for i, params in enumerate(combinations):
            result = self._run_single(params)
            results.append(result)
            log.debug(
                "sweep_combo_complete",
                index=i + 1,
                total=len(combinations),
                params=params,
                pnl=result.metrics.total_pnl,
            )

        # Sort by metric (descending — higher is better)
        results.sort(key=lambda r: getattr(r.metrics, self._rank_by), reverse=True)

        log.info("sweep_complete", total_results=len(results))

        return SweepResult(
            results=results,
            rank_metric=self._rank_by,
            symbol=self._config.symbol,
            total_combinations=len(combinations),
        )

    def _generate_combinations(self) -> list[dict[str, int | float]]:
        """Generate all valid parameter combinations from the grid."""
        keys = list(self._param_grid.keys())
        values = [self._param_grid[k] for k in keys]

        all_combos = [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*values)]

        # Filter invalid combinations
        if self._param_validator is not None:
            valid = [c for c in all_combos if self._param_validator(c)]
        else:
            valid = all_combos

        filtered = len(all_combos) - len(valid)
        if filtered > 0:
            log.info("sweep_filtered_invalid", count=filtered)

        return valid

    def _run_single(self, params: dict[str, int | float]) -> BacktestResult:
        """Run a single backtest with the given parameters."""
        strategy = self._strategy_factory(params)
        bar_count = strategy.min_bars

        market_data = HistoricalMarketDataAdapter(self._bars, bar_count=bar_count)
        broker = SimulatedBrokerAdapter(
            initial_capital=self._config.initial_capital,
            spread=self._config.spread_pips,
            slippage=self._config.slippage_pips,
            commission_per_trade=self._config.commission_per_trade,
            seed=self._config.deterministic_seed,
            grid_mode=hasattr(strategy, "report_fill"),
        )
        repository = InMemoryRepository()

        config = BacktestConfig(
            symbol=self._config.symbol,
            initial_capital=self._config.initial_capital,
            position_size=self._config.position_size,
            spread_pips=self._config.spread_pips,
            slippage_pips=self._config.slippage_pips,
            commission_per_trade=self._config.commission_per_trade,
            deterministic_seed=self._config.deterministic_seed,
            bar_count=bar_count,
        )

        runner = BacktestRunner(
            strategy=strategy,
            risk_engine=self._risk_engine,
            market_data=market_data,
            broker=broker,
            repository=repository,
            config=config,
            user_id=self._user_id,
        )

        result = runner.run()

        # Attach parameters to the result
        return BacktestResult(
            metrics=result.metrics,
            equity_curve=result.equity_curve,
            trades=result.trades,
            strategy_name=result.strategy_name,
            symbol=result.symbol,
            start_date=result.start_date,
            end_date=result.end_date,
            parameters={k: str(v) for k, v in params.items()},
        )
