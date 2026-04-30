"""FastAPI dependency injection callables."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from aurex_trade.web.tasks import TaskRegistry


def get_task_registry(request: Request) -> TaskRegistry:
    """Retrieve the TaskRegistry singleton from app state."""
    registry: TaskRegistry = request.app.state.task_registry
    return registry
