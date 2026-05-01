"""Shared runner factories for backtest, sweep, and walk-forward tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from aurex_trade.web.schemas import BacktestRequest, SweepRequest, WalkForwardRequest


def create_backtest_runner(req: BacktestRequest) -> Callable[[], object]:
    """Create a callable that runs a single backtest with the given parameters."""

    def run() -> object:
        from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
        from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
        from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
        from aurex_trade.adapters.memory.repository import InMemoryRepository
        from aurex_trade.backtest.config import BacktestConfig
        from aurex_trade.backtest.runner import BacktestRunner
        from aurex_trade.domain.risk.engine import RiskEngine
        from aurex_trade.domain.strategy.sma_crossover import SMACrossover

        config = BacktestConfig(
            symbol=req.symbol,
            granularity=req.granularity,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.capital,
            position_size=req.position_size,
            spread_pips=req.spread,
            slippage_pips=req.slippage,
            commission_per_trade=req.commission,
            deterministic_seed=req.seed,
            data_dir=Path("data/historical"),
            bar_count=req.long_window + 5,
        )

        data_store = HistoricalDataStore(config.data_dir)
        start = (
            datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.start_date
            else None
        )
        end = (
            datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.end_date
            else None
        )
        bars = data_store.load_bars(config.symbol, config.granularity, start, end)
        if not bars:
            msg = f"No data found for {config.symbol} ({config.granularity})"
            raise FileNotFoundError(msg)

        strategy = SMACrossover(short_window=req.short_window, long_window=req.long_window)
        risk_engine = RiskEngine(
            max_position_size=req.max_position,
            max_daily_loss=req.max_daily_loss,
            max_trades_per_day=req.max_trades_per_day,
            require_stop_loss=req.require_stop_loss,
            risk_per_trade=req.risk_per_trade,
            max_drawdown_pct=req.max_drawdown_pct,
            max_consecutive_losses=req.max_consecutive_losses,
        )
        market_data = HistoricalMarketDataAdapter(bars, config.bar_count)
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

        # Attach strategy parameters (runner leaves them empty)
        from aurex_trade.backtest.results import BacktestResult

        return BacktestResult(
            metrics=result.metrics,
            equity_curve=result.equity_curve,
            trades=result.trades,
            strategy_name=result.strategy_name,
            symbol=result.symbol,
            start_date=result.start_date,
            end_date=result.end_date,
            parameters={
                "short_window": str(req.short_window),
                "long_window": str(req.long_window),
            },
        )

    return run


def create_sweep_runner(req: SweepRequest) -> Callable[[], object]:
    """Create a callable that runs a parameter sweep with the given parameters."""

    def run() -> object:
        from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
        from aurex_trade.backtest.cli import PARAM_VALIDATORS, STRATEGY_REGISTRY
        from aurex_trade.backtest.config import BacktestConfig
        from aurex_trade.backtest.sweep import ParameterSweep
        from aurex_trade.domain.risk.engine import RiskEngine

        if req.strategy not in STRATEGY_REGISTRY:
            msg = f"Unknown strategy: {req.strategy}"
            raise ValueError(msg)

        config = BacktestConfig(
            symbol=req.symbol,
            granularity=req.granularity,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.capital,
            position_size=req.position_size,
            spread_pips=req.spread,
            slippage_pips=req.slippage,
            commission_per_trade=req.commission,
            deterministic_seed=req.seed,
            data_dir=Path("data/historical"),
        )

        data_store = HistoricalDataStore(config.data_dir)
        start = (
            datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.start_date
            else None
        )
        end = (
            datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.end_date
            else None
        )
        bars = data_store.load_bars(config.symbol, config.granularity, start, end)
        if not bars:
            msg = f"No data found for {config.symbol} ({config.granularity})"
            raise FileNotFoundError(msg)

        risk_engine = RiskEngine(
            max_position_size=req.max_position,
            max_daily_loss=req.max_daily_loss,
            max_trades_per_day=req.max_trades_per_day,
            require_stop_loss=req.require_stop_loss,
            risk_per_trade=req.risk_per_trade,
            max_drawdown_pct=req.max_drawdown_pct,
            max_consecutive_losses=req.max_consecutive_losses,
        )

        sweep = ParameterSweep(
            strategy_factory=STRATEGY_REGISTRY[req.strategy],
            param_grid=req.params,
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            rank_by=req.rank_by,
            param_validator=PARAM_VALIDATORS.get(req.strategy),
        )
        return sweep.run()

    return run


def create_walk_forward_runner(req: WalkForwardRequest) -> Callable[[], object]:
    """Create a callable that runs walk-forward validation with the given parameters."""

    def run() -> object:
        from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
        from aurex_trade.backtest.cli import PARAM_VALIDATORS, STRATEGY_REGISTRY
        from aurex_trade.backtest.config import BacktestConfig
        from aurex_trade.backtest.walk_forward import WalkForwardValidator
        from aurex_trade.domain.risk.engine import RiskEngine

        if req.strategy not in STRATEGY_REGISTRY:
            msg = f"Unknown strategy: {req.strategy}"
            raise ValueError(msg)

        config = BacktestConfig(
            symbol=req.symbol,
            granularity=req.granularity,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.capital,
            position_size=req.position_size,
            spread_pips=req.spread,
            slippage_pips=req.slippage,
            commission_per_trade=req.commission,
            deterministic_seed=req.seed,
            data_dir=Path("data/historical"),
        )

        data_store = HistoricalDataStore(config.data_dir)
        start = (
            datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.start_date
            else None
        )
        end = (
            datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.end_date
            else None
        )
        bars = data_store.load_bars(config.symbol, config.granularity, start, end)
        if not bars:
            msg = f"No data found for {config.symbol} ({config.granularity})"
            raise FileNotFoundError(msg)

        risk_engine = RiskEngine(
            max_position_size=req.max_position,
            max_daily_loss=req.max_daily_loss,
            max_trades_per_day=req.max_trades_per_day,
            require_stop_loss=req.require_stop_loss,
            risk_per_trade=req.risk_per_trade,
            max_drawdown_pct=req.max_drawdown_pct,
            max_consecutive_losses=req.max_consecutive_losses,
        )

        validator = WalkForwardValidator(
            strategy_factory=STRATEGY_REGISTRY[req.strategy],
            param_grid=req.params,
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            train_bars=req.train_bars,
            test_bars=req.test_bars,
            rank_by=req.rank_by,
            param_validator=PARAM_VALIDATORS.get(req.strategy),
        )
        return validator.run()

    return run
