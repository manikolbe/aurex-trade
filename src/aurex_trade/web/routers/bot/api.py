"""Bot control endpoints — start, stop, status."""

from __future__ import annotations

import threading

import structlog
from fastapi import APIRouter, Depends, Request

from aurex_trade.web.dependencies import get_task_registry
from aurex_trade.web.schemas import BotStatusResponse
from aurex_trade.web.tasks import TaskRegistry, TaskStatus

logger = structlog.get_logger()

router = APIRouter(prefix="/api/bot", tags=["bot"])

_bot_lock = threading.Lock()


@router.get("/status")
def bot_status(request: Request) -> BotStatusResponse:
    """Get current bot running status."""
    with _bot_lock:
        task_id = getattr(request.app.state, "bot_task_id", None)
        if task_id is None:
            return BotStatusResponse(running=False, task_id=None)

        registry: TaskRegistry = request.app.state.task_registry
        info = registry.get(task_id)
        running = info is not None and info.status == TaskStatus.RUNNING
        return BotStatusResponse(running=running, task_id=task_id)


@router.post("/start")
def start_bot(
    request: Request,
    registry: TaskRegistry = Depends(get_task_registry),
) -> BotStatusResponse:
    """Start the trading bot in background."""
    with _bot_lock:
        existing_id = getattr(request.app.state, "bot_task_id", None)
        if existing_id is not None:
            info = registry.get(existing_id)
            if info is not None and info.status == TaskStatus.RUNNING:
                return BotStatusResponse(running=True, task_id=existing_id)

        # TODO: Wire TradingEngine here once app.py exposes a factory function.
        # For now, return not-running status as this is a scaffold.
        logger.warning("bot.start_not_implemented")
        return BotStatusResponse(running=False, task_id=None)


@router.post("/stop")
def stop_bot(request: Request) -> BotStatusResponse:
    """Stop the trading bot."""
    with _bot_lock:
        task_id = getattr(request.app.state, "bot_task_id", None)
        if task_id is None:
            return BotStatusResponse(running=False, task_id=None)

        # NOTE: Graceful stop requires the engine to expose a stop() method.
        # For now, we just clear the reference. Full implementation needs
        # the engine instance stored in app.state.
        request.app.state.bot_task_id = None
        logger.info("bot.stop_requested")
        return BotStatusResponse(running=False, task_id=None)
