"""Bot control endpoints — start, stop, status, metrics."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

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
from aurex_trade.web.schemas import BotMetricsResponse, BotStartRequest, BotStatusResponse
from aurex_trade.web.tasks import TaskRegistry

logger = structlog.get_logger()

router = APIRouter(prefix="/api/bot", tags=["bot"])


def _build_status(
    session_manager: BotSessionManager, user_id: str
) -> BotStatusResponse:
    """Build a BotStatusResponse from the current session state."""
    session = session_manager.get(user_id)
    if session is None:
        return BotStatusResponse(running=False)

    metrics = session.engine.get_metrics()
    return BotStatusResponse(
        running=True,
        symbol=session.symbol,
        strategy_name=session.strategy_name,
        started_at=session.started_at,
        metrics=BotMetricsResponse(**metrics),
    )


@router.get("/status")
def bot_status(
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> BotStatusResponse:
    """Get current bot running status with metrics."""
    return _build_status(session_manager, user.id)


@router.post("/start", response_model=None)
@limiter.limit(ratelimit_config.bot_control)
def start_bot(
    request: Request,
    body: BotStartRequest,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
    credential_store: FernetCredentialStore = Depends(get_credential_store),
    registry: TaskRegistry = Depends(get_task_registry),
) -> BotStatusResponse | JSONResponse:
    """Start the trading bot in background."""
    if session_manager.is_running(user.id):
        return JSONResponse(
            status_code=409,
            content=BotStatusResponse(
                running=True, error="Bot already running"
            ).model_dump(mode="json"),
        )

    try:
        start_bot_session(
            user_id=user.id,
            body=body,
            session_manager=session_manager,
            credential_store=credential_store,
            registry=registry,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content=BotStatusResponse(running=False, error=str(exc)).model_dump(
                mode="json"
            ),
        )
    except BotAlreadyRunningError as exc:
        return JSONResponse(
            status_code=409,
            content=BotStatusResponse(running=True, error=str(exc)).model_dump(
                mode="json"
            ),
        )

    return _build_status(session_manager, user.id)


@router.post("/stop")
@limiter.limit(ratelimit_config.bot_control)
def stop_bot(
    request: Request,
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> BotStatusResponse:
    """Stop the trading bot."""
    session_manager.stop(user.id)
    logger.info("bot.stop_requested", user_id=user.id)
    return BotStatusResponse(running=False)


@router.get("/metrics", response_model=None)
def bot_metrics(
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> BotMetricsResponse | JSONResponse:
    """Get live metrics from a running bot."""
    session = session_manager.get(user.id)
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "No bot running"})
    return BotMetricsResponse(**session.engine.get_metrics())


@router.get("/equity", response_model=None)
def bot_equity(
    user: User = Depends(get_current_user),
    session_manager: BotSessionManager = Depends(get_bot_session_manager),
) -> JSONResponse:
    """Get equity history and trade markers for charting."""
    session = session_manager.get(user.id)
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "No bot running"})
    return JSONResponse(content={
        "equity_history": session.engine.get_equity_history(),
        "trade_markers": session.engine.get_trade_markers(),
    })
