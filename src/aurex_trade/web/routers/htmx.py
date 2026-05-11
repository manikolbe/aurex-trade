"""HTMX endpoints that return HTML fragments for the backtest UI."""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
from aurex_trade.domain.models import User
from aurex_trade.web._defaults_helpers import save_preferred_and_risk, save_user_defaults
from aurex_trade.web._run_helpers import (
    create_backtest_runner,
    create_sweep_runner,
    create_walk_forward_runner,
)
from aurex_trade.web.auth.dependencies import get_current_user
from aurex_trade.web.dependencies import get_task_registry, get_user_defaults_store
from aurex_trade.web.schemas import (
    BacktestRequest,
    SweepRequest,
    WalkForwardRequest,
    backtest_result_to_response,
    sweep_result_to_response,
    walk_forward_result_to_response,
)
from aurex_trade.web.tasks import TaskRegistry, TaskStatus

logger = structlog.get_logger()

router = APIRouter(prefix="/htmx", tags=["htmx"])


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


# --- Backtest ---


@router.post("/backtest/submit", response_class=HTMLResponse)
def htmx_submit_backtest(
    request: Request,
    req: BacktestRequest,
    user: User = Depends(get_current_user),
    registry: TaskRegistry = Depends(get_task_registry),
    defaults_store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> HTMLResponse:
    """Submit a backtest and return a loading fragment that polls for results."""
    task_id = uuid4()
    runner = create_backtest_runner(req, task_id=task_id, registry=registry, user_id=user.id)
    registry.submit(runner, task_type="backtest", task_id=task_id)
    save_user_defaults(defaults_store, user.id, req)
    logger.info("htmx.backtest.submitted", task_id=str(task_id))
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request, "partials/backtest_loading.html", {"task_id": task_id, "message": None}
    )


@router.get("/backtest/{task_id}/poll", response_class=HTMLResponse)
def htmx_poll_backtest(
    request: Request,
    task_id: UUID,
    registry: TaskRegistry = Depends(get_task_registry),
) -> HTMLResponse:
    """Poll backtest status. Returns loading/result/error partial."""
    templates = _get_templates(request)
    info = registry.get(task_id)

    if info is None:
        return templates.TemplateResponse(
            request, "partials/backtest_error.html", {"error": "Task not found"}
        )

    if info.status == TaskStatus.FAILED:
        return templates.TemplateResponse(
            request, "partials/backtest_error.html", {"error": info.error or "Unknown error"}
        )

    if info.status == TaskStatus.COMPLETED and info.result is not None:
        from aurex_trade.backtest.results import BacktestResult

        result: BacktestResult = info.result  # type: ignore[assignment]
        response = backtest_result_to_response(result)
        return templates.TemplateResponse(
            request, "partials/backtest_result.html", {"result": response}
        )

    return templates.TemplateResponse(
        request, "partials/backtest_loading.html", {"task_id": task_id, "message": info.message}
    )


# --- Sweep ---


@router.post("/sweep/submit", response_class=HTMLResponse)
def htmx_submit_sweep(
    request: Request,
    req: SweepRequest,
    user: User = Depends(get_current_user),
    registry: TaskRegistry = Depends(get_task_registry),
    defaults_store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> HTMLResponse:
    """Submit a sweep and return a loading fragment that polls for results."""
    task_id = uuid4()
    runner = create_sweep_runner(req, task_id=task_id, registry=registry, user_id=user.id)
    registry.submit(runner, task_type="sweep", task_id=task_id)
    save_preferred_and_risk(defaults_store, user.id, req)
    logger.info("htmx.sweep.submitted", task_id=str(task_id))
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request, "partials/sweep_loading.html", {"task_id": task_id, "message": None}
    )


@router.get("/sweep/{task_id}/poll", response_class=HTMLResponse)
def htmx_poll_sweep(
    request: Request,
    task_id: UUID,
    registry: TaskRegistry = Depends(get_task_registry),
) -> HTMLResponse:
    """Poll sweep status. Returns loading/result/error partial."""
    templates = _get_templates(request)
    info = registry.get(task_id)

    if info is None:
        return templates.TemplateResponse(
            request, "partials/sweep_error.html", {"error": "Task not found"}
        )

    if info.status == TaskStatus.FAILED:
        return templates.TemplateResponse(
            request, "partials/sweep_error.html", {"error": info.error or "Unknown error"}
        )

    if info.status == TaskStatus.COMPLETED and info.result is not None:
        from aurex_trade.backtest.results import SweepResult

        result: SweepResult = info.result  # type: ignore[assignment]
        response = sweep_result_to_response(result)
        return templates.TemplateResponse(
            request, "partials/sweep_result.html", {"result": response}
        )

    return templates.TemplateResponse(
        request, "partials/sweep_loading.html", {"task_id": task_id, "message": info.message}
    )


# --- Walk-Forward ---


@router.post("/walk-forward/submit", response_class=HTMLResponse)
def htmx_submit_walk_forward(
    request: Request,
    req: WalkForwardRequest,
    user: User = Depends(get_current_user),
    registry: TaskRegistry = Depends(get_task_registry),
    defaults_store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> HTMLResponse:
    """Submit walk-forward validation and return a loading fragment."""
    task_id = uuid4()
    runner = create_walk_forward_runner(req, task_id=task_id, registry=registry, user_id=user.id)
    registry.submit(runner, task_type="walk_forward", task_id=task_id)
    save_preferred_and_risk(defaults_store, user.id, req)
    logger.info("htmx.walk_forward.submitted", task_id=str(task_id))
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request, "partials/wf_loading.html", {"task_id": task_id, "message": None}
    )


@router.get("/walk-forward/{task_id}/poll", response_class=HTMLResponse)
def htmx_poll_walk_forward(
    request: Request,
    task_id: UUID,
    registry: TaskRegistry = Depends(get_task_registry),
) -> HTMLResponse:
    """Poll walk-forward status. Returns loading/result/error partial."""
    templates = _get_templates(request)
    info = registry.get(task_id)

    if info is None:
        return templates.TemplateResponse(
            request, "partials/wf_error.html", {"error": "Task not found"}
        )

    if info.status == TaskStatus.FAILED:
        return templates.TemplateResponse(
            request, "partials/wf_error.html", {"error": info.error or "Unknown error"}
        )

    if info.status == TaskStatus.COMPLETED and info.result is not None:
        from aurex_trade.backtest.results import WalkForwardResult

        result: WalkForwardResult = info.result  # type: ignore[assignment]
        response = walk_forward_result_to_response(result)
        return templates.TemplateResponse(
            request, "partials/wf_result.html", {"result": response}
        )

    return templates.TemplateResponse(
        request, "partials/wf_loading.html", {"task_id": task_id, "message": info.message}
    )
