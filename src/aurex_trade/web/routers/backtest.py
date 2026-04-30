"""Backtest, sweep, and walk-forward API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException

from aurex_trade.web.dependencies import get_task_registry
from aurex_trade.web.schemas import (
    BacktestRequest,
    BacktestResultResponse,
    SweepRequest,
    SweepResultResponse,
    TaskStatusResponse,
    TaskSubmittedResponse,
    WalkForwardRequest,
    WalkForwardResultResponse,
    backtest_result_to_response,
    sweep_result_to_response,
    task_info_to_response,
    walk_forward_result_to_response,
)
from aurex_trade.web.tasks import TaskRegistry, TaskStatus

logger = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["backtest"])


@router.post("/backtest", status_code=202)
def submit_backtest(
    req: BacktestRequest,
    registry: TaskRegistry = Depends(get_task_registry),
) -> TaskSubmittedResponse:
    """Submit a backtest for background execution."""

    def run_backtest() -> object:
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
        return runner.run()

    task_id = registry.submit(run_backtest, task_type="backtest")
    logger.info("backtest.submitted", task_id=str(task_id))
    return TaskSubmittedResponse(task_id=task_id, task_type="backtest", status=TaskStatus.RUNNING)


@router.get("/backtest/{task_id}")
def get_backtest_status(
    task_id: UUID,
    registry: TaskRegistry = Depends(get_task_registry),
) -> TaskStatusResponse | BacktestResultResponse:
    """Poll backtest task status. Returns result when completed."""
    info = registry.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if info.status == TaskStatus.COMPLETED and info.result is not None:
        from aurex_trade.backtest.results import BacktestResult

        result: BacktestResult = info.result  # type: ignore[assignment]
        return backtest_result_to_response(result)

    return task_info_to_response(info)


@router.post("/sweep", status_code=202)
def submit_sweep(
    req: SweepRequest,
    registry: TaskRegistry = Depends(get_task_registry),
) -> TaskSubmittedResponse:
    """Submit a parameter sweep for background execution."""

    def run_sweep() -> object:
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

    task_id = registry.submit(run_sweep, task_type="sweep")
    logger.info("sweep.submitted", task_id=str(task_id))
    return TaskSubmittedResponse(task_id=task_id, task_type="sweep", status=TaskStatus.RUNNING)


@router.get("/sweep/{task_id}")
def get_sweep_status(
    task_id: UUID,
    registry: TaskRegistry = Depends(get_task_registry),
) -> TaskStatusResponse | SweepResultResponse:
    """Poll sweep task status. Returns result when completed."""
    info = registry.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if info.status == TaskStatus.COMPLETED and info.result is not None:
        from aurex_trade.backtest.results import SweepResult

        result: SweepResult = info.result  # type: ignore[assignment]
        return sweep_result_to_response(result)

    return task_info_to_response(info)


@router.post("/walk-forward", status_code=202)
def submit_walk_forward(
    req: WalkForwardRequest,
    registry: TaskRegistry = Depends(get_task_registry),
) -> TaskSubmittedResponse:
    """Submit a walk-forward validation for background execution."""

    def run_walk_forward() -> object:
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

    task_id = registry.submit(run_walk_forward, task_type="walk_forward")
    logger.info("walk_forward.submitted", task_id=str(task_id))
    return TaskSubmittedResponse(
        task_id=task_id, task_type="walk_forward", status=TaskStatus.RUNNING
    )


@router.get("/walk-forward/{task_id}")
def get_walk_forward_status(
    task_id: UUID,
    registry: TaskRegistry = Depends(get_task_registry),
) -> TaskStatusResponse | WalkForwardResultResponse:
    """Poll walk-forward task status. Returns result when completed."""
    info = registry.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Task not found")

    if info.status == TaskStatus.COMPLETED and info.result is not None:
        from aurex_trade.backtest.results import WalkForwardResult

        result: WalkForwardResult = info.result  # type: ignore[assignment]
        return walk_forward_result_to_response(result)

    return task_info_to_response(info)
