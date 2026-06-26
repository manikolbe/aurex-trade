"""Walk-forward validation — rolling train/test windows to prevent overfitting.

Uses ParameterSweep on training windows, then validates best params on
unseen test data. Non-overlapping windows by default.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.results import BacktestResult, WalkForwardResult, WalkForwardWindow
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.backtest.sweep import ParameterSweep
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy
from aurex_trade.metrics import PerformanceMetrics, calculate_metrics

log = structlog.get_logger()


class WalkForwardValidator:
    """Rolling train/test validation across non-overlapping windows.

    For each window:
    1. Run ParameterSweep on training bars → find best params
    2. Run single backtest with those params on test bars (unseen data)
    3. Record both results

    Default: 7200 train bars + 7200 test bars = 1 week each (M1 bars).
    """

    def __init__(
        self,
        strategy_factory: Callable[[dict[str, int | float]], Strategy],
        param_grid: dict[str, list[int | float]],
        bars: list[BarData],
        config: BacktestConfig,
        risk_engine: RiskEngine,
        train_bars: int = 7200,
        test_bars: int = 7200,
        rank_by: str = "total_pnl",
        param_validator: Callable[[dict[str, int | float]], bool] | None = None,
        min_trades: int = 30,
        *,
        user_id: str,
    ) -> None:
        self._strategy_factory = strategy_factory
        self._param_grid = param_grid
        self._bars = bars
        self._config = config
        self._risk_engine = risk_engine
        self._train_bars = train_bars
        self._test_bars = test_bars
        self._rank_by = rank_by
        self._param_validator = param_validator
        self._min_trades = min_trades
        self._user_id = user_id

    def run(self) -> WalkForwardResult:
        """Run walk-forward validation across non-overlapping windows."""
        window_size = self._train_bars + self._test_bars
        num_windows = len(self._bars) // window_size
        windows: list[WalkForwardWindow] = []

        log.info(
            "walk_forward_started",
            total_bars=len(self._bars),
            train_bars=self._train_bars,
            test_bars=self._test_bars,
            num_windows=num_windows,
        )

        for i in range(num_windows):
            offset = i * window_size
            train_slice = self._bars[offset : offset + self._train_bars]
            test_slice = self._bars[offset + self._train_bars : offset + window_size]

            log.info(
                "walk_forward_window",
                window=i + 1,
                total=num_windows,
                train_start=train_slice[0].timestamp.isoformat(),
                test_end=test_slice[-1].timestamp.isoformat(),
            )

            # Step 1: Sweep on training data
            sweep = ParameterSweep(
                strategy_factory=self._strategy_factory,
                param_grid=self._param_grid,
                bars=train_slice,
                config=self._config,
                risk_engine=self._risk_engine,
                rank_by=self._rank_by,
                param_validator=self._param_validator,
                min_trades=self._min_trades,
                user_id=self._user_id,
            )
            sweep_result = sweep.run()

            if not sweep_result.results:
                log.warning("walk_forward_no_results", window=i + 1)
                continue

            # Best params from training
            best_train = sweep_result.results[0]
            best_params: dict[str, int | float] = {}
            for k, v in best_train.parameters.items():
                try:
                    best_params[k] = int(v)
                except ValueError:
                    best_params[k] = float(v)

            # Step 2: Run best params on test data (unseen)
            test_result = self._run_test(best_params, test_slice)

            windows.append(
                WalkForwardWindow(
                    train_result=best_train,
                    test_result=test_result,
                    best_params=best_params,
                    window_index=i,
                )
            )

            log.info(
                "walk_forward_window_complete",
                window=i + 1,
                best_params=best_params,
                train_pnl=best_train.metrics.total_pnl,
                test_pnl=test_result.metrics.total_pnl,
            )

        # Aggregate test metrics across all windows
        aggregate = self._aggregate_test_metrics(windows)

        # Determine strategy name from the last successful window
        strategy_name = ""
        if windows:
            strategy_name = windows[-1].test_result.strategy_name
        else:
            # No windows produced results — create strategy for name only
            sample = {k: v[0] for k, v in self._param_grid.items()}
            strategy_name = self._strategy_factory(sample).name

        log.info(
            "walk_forward_complete",
            windows=len(windows),
            aggregate_pnl=aggregate.total_pnl,
            aggregate_sharpe=aggregate.sharpe_ratio,
        )

        return WalkForwardResult(
            windows=windows,
            aggregate_test_metrics=aggregate,
            strategy_name=strategy_name,
            symbol=self._config.symbol,
        )

    def _run_test(self, params: dict[str, int | float], bars: list[BarData]) -> BacktestResult:
        """Run a single backtest with given params on test bars."""

        strategy = self._strategy_factory(params)
        bar_count = strategy.min_bars

        market_data = HistoricalMarketDataAdapter(bars, bar_count=bar_count)
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

        return BacktestResult(
            metrics=result.metrics,
            equity_curve=result.equity_curve,
            trades=result.trades,
            strategy_name=result.strategy_name,
            symbol=result.symbol,
            start_date=result.start_date,
            end_date=result.end_date,
            parameters={k: str(v) for k, v in params.items()},
            trade_pnls=result.trade_pnls,
        )

    def _aggregate_test_metrics(self, windows: list[WalkForwardWindow]) -> PerformanceMetrics:
        """Combine test results across all windows into aggregate metrics."""
        if not windows:
            return calculate_metrics(
                equity_curve=[self._config.initial_capital],
                trade_pnls=[],
                initial_capital=self._config.initial_capital,
            )

        # Chain the per-window test equity curves (each scaled to start where the
        # prior left off) and concatenate the REAL per-trade P&L from every window.
        # Earlier code fabricated synthetic +/- placeholders to match win/loss
        # counts, which made aggregate profit_factor and expectancy meaningless;
        # the true distribution comes straight from each window's trade_pnls.
        combined_equity: list[float] = [self._config.initial_capital]
        combined_pnls: list[float] = []
        total_commission = 0.0
        running_capital = self._config.initial_capital

        for window in windows:
            test_metrics = window.test_result.metrics
            if len(window.test_result.equity_curve) > 1:
                scale = running_capital / test_metrics.initial_capital
                for eq in window.test_result.equity_curve[1:]:
                    combined_equity.append(eq * scale)

            combined_pnls.extend(window.test_result.trade_pnls)
            total_commission += test_metrics.total_commission
            running_capital += test_metrics.total_pnl

        return calculate_metrics(
            equity_curve=combined_equity,
            trade_pnls=combined_pnls,
            initial_capital=self._config.initial_capital,
            total_commission=total_commission,
        )
