"""HTMX endpoints that return HTML fragments for the bot UI."""

from __future__ import annotations

import functools
import json

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from aurex_trade.adapters.sqlite.credential_store import FernetCredentialStore
from aurex_trade.domain.models import User
from aurex_trade.web._bot_sessions import BotAlreadyRunningError, BotSessionManager
from aurex_trade.web.auth.dependencies import get_current_user
from aurex_trade.web.dependencies import (
    get_bot_session_manager,
    get_credential_store,
    get_task_registry,
)
from aurex_trade.web.ratelimit import limiter, ratelimit_config
from aurex_trade.web.routers.bot._common import start_bot_session
from aurex_trade.web.schemas import BotStartRequest
from aurex_trade.web.tasks import TaskRegistry

logger = structlog.get_logger()

router = APIRouter(prefix="/htmx/bot", tags=["bot-htmx"])


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


@functools.cache
def _get_strategies_json() -> str:
    """Get strategies metadata as JSON (cached — strategies are static at runtime)."""
    from aurex_trade.web.app import _get_strategies_context

    return json.dumps(_get_strategies_context())


async def _parse_start_form(request: Request) -> BotStartRequest:
    """Parse bot start request from form data."""
    form = await request.form()
    body: dict[
        str, str | int | float | dict[str, int | float] | dict[str, int | float | bool]
    ] = {}

    body["strategy_name"] = str(form.get("strategy_name", ""))
    body["symbol"] = str(form.get("symbol", "XAU_USD"))
    body["granularity"] = str(form.get("granularity", "M1"))

    interval = str(form.get("interval_seconds", "60"))
    body["interval_seconds"] = int(interval) if interval else 60

    # Parse strategy_params from individual form fields (param_<key>=<value>)
    strategy_params: dict[str, int | float] = {}
    for key, value in form.items():
        if key.startswith("param_") and value:
            param_name = key[len("param_"):]
            str_val = str(value)
            try:
                strategy_params[param_name] = int(str_val)
            except ValueError:
                strategy_params[param_name] = float(str_val)
    body["strategy_params"] = strategy_params

    # Parse risk_params from individual form fields (risk_<key>=<value>)
    risk_params: dict[str, int | float | bool] = {}
    for key, value in form.items():
        if key.startswith("risk_") and value:
            param_name = key[len("risk_"):]
            str_val = str(value)
            if str_val.lower() in ("true", "false"):
                risk_params[param_name] = str_val.lower() == "true"
            else:
                try:
                    risk_params[param_name] = int(str_val)
                except ValueError:
                    risk_params[param_name] = float(str_val)
    body["risk_params"] = risk_params

    return BotStartRequest(**body)  # type: ignore[arg-type]


@router.post("/start", response_class=HTMLResponse)
@limiter.limit(ratelimit_config.bot_control)
async def htmx_start_bot(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
    credential_store: FernetCredentialStore = Depends(get_credential_store),
    registry: TaskRegistry = Depends(get_task_registry),
) -> HTMLResponse:
    """Start the bot and return a running status partial."""
    templates = _get_templates(request)

    try:
        body = await _parse_start_form(request)
    except (ValidationError, ValueError) as exc:
        return templates.TemplateResponse(
            request, "partials/bot_error.html", {"error": str(exc)}
        )

    try:
        session = start_bot_session(
            user_id=user.id,
            body=body,
            session_manager=session_manager,
            credential_store=credential_store,
            registry=registry,
        )
    except (ValueError, BotAlreadyRunningError) as exc:
        return templates.TemplateResponse(
            request, "partials/bot_error.html", {"error": str(exc)}
        )

    return templates.TemplateResponse(
        request,
        "partials/bot_running.html",
        {
            "symbol": body.symbol,
            "strategy_name": body.strategy_name,
            "started_at": session.started_at,
            "metrics": None,
            "kill_switch_active": False,
            "granularity": session.granularity,
            "strategy_params": session.strategy_params,
            "risk_params": session.risk_params,
            "equity_history": [],
        },
    )


@router.post("/stop", response_class=HTMLResponse)
@limiter.limit(ratelimit_config.bot_control)
def htmx_stop_bot(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> HTMLResponse:
    """Stop the bot and redirect to bot page (avoids HTMX swap race)."""
    session_manager.stop(user.id)
    logger.info("htmx.bot.stop_requested", user_id=user.id)
    response = HTMLResponse(content="", status_code=200)
    response.headers["HX-Redirect"] = "/bot"
    return response


@router.get("/state-check", response_class=HTMLResponse)
def htmx_state_check(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> HTMLResponse:
    """Lightweight check — returns idle partial if bot stopped, 204 if still running."""
    templates = _get_templates(request)
    session = session_manager.get(user.id)

    if session is None or not session.engine.get_metrics()["running"]:
        return templates.TemplateResponse(
            request, "partials/bot_idle.html", {"strategies_json": _get_strategies_json()}
        )

    return HTMLResponse(status_code=204)


@router.get("/status/poll", response_class=HTMLResponse)
def htmx_poll_status(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> HTMLResponse:
    """Poll bot status — returns idle, running, or error partial."""
    templates = _get_templates(request)
    session = session_manager.get(user.id)

    if session is None:
        return templates.TemplateResponse(
            request, "partials/bot_idle.html", {"strategies_json": _get_strategies_json()}
        )

    metrics = session.engine.get_metrics()

    if not metrics["running"]:
        return templates.TemplateResponse(
            request, "partials/bot_idle.html", {"strategies_json": _get_strategies_json()}
        )

    return templates.TemplateResponse(
        request,
        "partials/bot_running.html",
        {
            "symbol": session.symbol,
            "strategy_name": session.strategy_name,
            "started_at": session.started_at,
            "metrics": metrics,
            "kill_switch_active": session.engine.kill_switch,
            "granularity": session.granularity,
            "strategy_params": session.strategy_params,
            "risk_params": session.risk_params,
            "equity_history": session.engine.get_equity_history(),
        },
    )


@router.post("/kill-switch", response_class=HTMLResponse)
@limiter.limit(ratelimit_config.bot_control)
def htmx_toggle_kill_switch(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> HTMLResponse:
    """Toggle the kill switch on a running bot."""
    templates = _get_templates(request)
    session = session_manager.get(user.id)

    if session is None:
        return templates.TemplateResponse(
            request, "partials/bot_error.html", {"error": "No bot is running."}
        )

    new_state = not session.engine.kill_switch
    session.engine.kill_switch = new_state
    logger.info("htmx.bot.kill_switch_toggled", user_id=user.id, active=new_state)

    metrics = session.engine.get_metrics()
    return templates.TemplateResponse(
        request,
        "partials/bot_running.html",
        {
            "symbol": session.symbol,
            "strategy_name": session.strategy_name,
            "started_at": session.started_at,
            "metrics": metrics,
            "kill_switch_active": new_state,
            "granularity": session.granularity,
            "strategy_params": session.strategy_params,
            "risk_params": session.risk_params,
            "equity_history": session.engine.get_equity_history(),
        },
    )


@router.get("/metrics/poll", response_class=HTMLResponse)
def htmx_poll_metrics(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> HTMLResponse:
    """Poll live metrics — returns metrics partial or empty."""
    templates = _get_templates(request)
    session = session_manager.get(user.id)

    if session is None:
        return HTMLResponse("")

    metrics = session.engine.get_metrics()
    return templates.TemplateResponse(
        request, "partials/bot_metrics.html", {"metrics": metrics}
    )


