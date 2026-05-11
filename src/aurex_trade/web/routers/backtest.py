"""Backtest, sweep, and walk-forward API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from aurex_trade.adapters.sqlite.market_data_store import (
    SQLiteMarketDataStore,
    UserDataPreferencesStore,
)
from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
from aurex_trade.domain.models import User
from aurex_trade.web._run_helpers import (
    create_backtest_runner,
    create_sweep_runner,
    create_walk_forward_runner,
)
from aurex_trade.web.auth.dependencies import get_current_user
from aurex_trade.web.dependencies import (
    get_market_data_store,
    get_preferences_store,
    get_task_registry,
    get_user_defaults_store,
)
from aurex_trade.web.schemas import (
    BacktestRequest,
    BacktestResultResponse,
    DataRangeResponse,
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


def _save_user_defaults(
    store: UserDefaultsStore, user_id: str, req: BacktestRequest
) -> None:
    """Auto-save strategy params and risk/cost settings from a backtest submit."""
    store.save_strategy_defaults(
        user_id, req.strategy, req.params, is_preferred=True
    )
    store.save_risk_defaults(user_id, _extract_risk_settings(req))


def _save_preferred_and_risk(
    store: UserDefaultsStore,
    user_id: str,
    req: SweepRequest | WalkForwardRequest,
) -> None:
    """Auto-save preferred strategy + risk/cost from sweep/walk-forward submit."""
    store.save_strategy_defaults(
        user_id, req.strategy, {}, is_preferred=True
    )
    store.save_risk_defaults(user_id, _extract_risk_settings(req))


def _extract_risk_settings(
    req: BacktestRequest | SweepRequest | WalkForwardRequest,
) -> dict[str, int | float | bool | str]:
    """Extract risk/cost fields + symbol/granularity from a request."""
    return {
        "symbol": req.symbol,
        "granularity": req.granularity,
        "max_position": req.max_position,
        "max_daily_loss": req.max_daily_loss,
        "risk_per_trade": req.risk_per_trade,
        "max_drawdown_pct": req.max_drawdown_pct,
        "max_trades_per_day": req.max_trades_per_day,
        "max_consecutive_losses": req.max_consecutive_losses,
        "require_stop_loss": req.require_stop_loss,
        "capital": req.capital,
        "position_size": req.position_size,
        "spread": req.spread,
        "slippage": req.slippage,
        "commission": req.commission,
        "seed": req.seed,
    }


@router.post("/backtest", status_code=202)
def submit_backtest(
    req: BacktestRequest,
    user: User = Depends(get_current_user),
    registry: TaskRegistry = Depends(get_task_registry),
    prefs_store: UserDataPreferencesStore = Depends(get_preferences_store),
    defaults_store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> TaskSubmittedResponse:
    """Submit a backtest for background execution."""
    task_id = uuid4()
    runner = create_backtest_runner(req, task_id=task_id, registry=registry, user_id=user.id)
    registry.submit(runner, task_type="backtest", task_id=task_id)
    if req.start_date and req.end_date:
        prefs_store.save_preference(
            user.id, req.symbol, req.granularity, req.start_date, req.end_date
        )
    _save_user_defaults(defaults_store, user.id, req)
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
    user: User = Depends(get_current_user),
    registry: TaskRegistry = Depends(get_task_registry),
    prefs_store: UserDataPreferencesStore = Depends(get_preferences_store),
    defaults_store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> TaskSubmittedResponse:
    """Submit a parameter sweep for background execution."""
    task_id = uuid4()
    runner = create_sweep_runner(req, task_id=task_id, registry=registry, user_id=user.id)
    registry.submit(runner, task_type="sweep", task_id=task_id)
    if req.start_date and req.end_date:
        prefs_store.save_preference(
            user.id, req.symbol, req.granularity, req.start_date, req.end_date
        )
    _save_preferred_and_risk(defaults_store, user.id, req)
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
    user: User = Depends(get_current_user),
    registry: TaskRegistry = Depends(get_task_registry),
    prefs_store: UserDataPreferencesStore = Depends(get_preferences_store),
    defaults_store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> TaskSubmittedResponse:
    """Submit a walk-forward validation for background execution."""
    task_id = uuid4()
    runner = create_walk_forward_runner(req, task_id=task_id, registry=registry, user_id=user.id)
    registry.submit(runner, task_type="walk_forward", task_id=task_id)
    if req.start_date and req.end_date:
        prefs_store.save_preference(
            user.id, req.symbol, req.granularity, req.start_date, req.end_date
        )
    _save_preferred_and_risk(defaults_store, user.id, req)
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


@router.get("/data-range")
def get_data_range(
    user: User = Depends(get_current_user),
    market_data_store: SQLiteMarketDataStore = Depends(get_market_data_store),
    prefs_store: UserDataPreferencesStore = Depends(get_preferences_store),
    symbol: str = Query(default="XAU_USD", pattern=r"^[A-Z0-9_]{1,20}$"),
    granularity: str = Query(default="M1", pattern=r"^[A-Z0-9]{1,3}$"),
) -> DataRangeResponse:
    """Return the preferred/available date range for a symbol/granularity.

    Priority: user preference > existing data coverage > safe default (2 weeks).
    """
    # 1. Check user preference first
    pref = prefs_store.get_preference(user.id, symbol, granularity)
    if pref is not None:
        return DataRangeResponse(
            start_date=pref[0],
            end_date=pref[1],
            source="preference",
        )

    # 2. Check existing data coverage
    date_range = market_data_store.get_date_range(symbol, granularity)
    if date_range is not None:
        return DataRangeResponse(
            start_date=date_range[0].strftime("%Y-%m-%d"),
            end_date=date_range[1].strftime("%Y-%m-%d"),
            source="existing",
        )

    # 3. No data — return safe default (last 2 weeks)
    today = datetime.now(tz=UTC).date()
    default_start = today - timedelta(days=14)
    return DataRangeResponse(
        start_date=default_start.isoformat(),
        end_date=today.isoformat(),
        source="default",
    )


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
