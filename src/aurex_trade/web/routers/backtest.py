"""Backtest, sweep, and walk-forward API endpoints."""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException

from aurex_trade.web._run_helpers import (
    create_backtest_runner,
    create_sweep_runner,
    create_walk_forward_runner,
)
from aurex_trade.web.dependencies import get_task_registry
from aurex_trade.web.schemas import (
    BacktestRequest,
    BacktestResultResponse,
    ParamMetaResponse,
    StrategiesResponse,
    StrategyInfoResponse,
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
    task_id = uuid4()
    runner = create_backtest_runner(req, task_id=task_id, registry=registry)
    registry.submit(runner, task_type="backtest", task_id=task_id)
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
    task_id = uuid4()
    runner = create_sweep_runner(req, task_id=task_id, registry=registry)
    registry.submit(runner, task_type="sweep", task_id=task_id)
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
    task_id = uuid4()
    runner = create_walk_forward_runner(req, task_id=task_id, registry=registry)
    registry.submit(runner, task_type="walk_forward", task_id=task_id)
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


@router.get("/strategies")
def list_strategies() -> StrategiesResponse:
    """Return all registered strategies with their parameter metadata."""
    from aurex_trade.backtest.cli import STRATEGY_METADATA

    strategies = []
    for name, meta_fn in STRATEGY_METADATA.items():
        meta = meta_fn()
        strategies.append(
            StrategyInfoResponse(
                name=name,
                display_name=meta.display_name,
                description=meta.description,
                params=[
                    ParamMetaResponse(
                        key=p.key,
                        label=p.label,
                        tooltip=p.tooltip,
                        default=p.default,
                        min_value=p.min_value,
                        max_value=p.max_value,
                    )
                    for p in meta.params
                ],
            )
        )
    return StrategiesResponse(strategies=strategies)
