"""Background task registry — in-process, dict-based, thread-safe."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

_logger = logging.getLogger(__name__)

_MAX_COMPLETED_TASKS = 100


class TaskStatus(StrEnum):
    """Status of a background task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskInfo:
    """Metadata and result for a background task."""

    id: UUID
    task_type: str
    status: TaskStatus
    created_at: datetime
    completed_at: datetime | None = None
    result: object = None
    error: str | None = None


class TaskRegistry:
    """Thread-safe registry for background tasks using ThreadPoolExecutor."""

    def __init__(self, max_workers: int = 2) -> None:
        self._tasks: dict[UUID, TaskInfo] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, fn: Callable[[], object], task_type: str) -> UUID:
        """Submit a callable for background execution. Returns task ID."""
        task_id = uuid4()
        info = TaskInfo(
            id=task_id,
            task_type=task_type,
            status=TaskStatus.PENDING,
            created_at=datetime.now(UTC),
        )

        with self._lock:
            self._tasks[task_id] = info

        future: Future[object] = self._executor.submit(fn)
        future.add_done_callback(lambda f: self._on_done(task_id, f))

        with self._lock:
            self._tasks[task_id].status = TaskStatus.RUNNING

        return task_id

    def get(self, task_id: UUID) -> TaskInfo | None:
        """Get task info by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def list_all(self) -> list[TaskInfo]:
        """List all tasks, most recent first."""
        with self._lock:
            return sorted(
                self._tasks.values(), key=lambda t: t.created_at, reverse=True
            )

    def shutdown(self) -> None:
        """Shut down the executor (wait for running tasks)."""
        self._executor.shutdown(wait=True)

    def _on_done(self, task_id: UUID, future: Future[object]) -> None:
        """Callback when a task completes or fails."""
        with self._lock:
            info = self._tasks.get(task_id)
            if info is None:
                _logger.error("Task %s not found in registry on completion", task_id)
                return

            info.completed_at = datetime.now(UTC)
            exc = future.exception()
            if exc is not None:
                info.status = TaskStatus.FAILED
                info.error = f"{type(exc).__name__}: {exc}"
            else:
                info.status = TaskStatus.COMPLETED
                info.result = future.result()

            self._evict_old_tasks()

    def _evict_old_tasks(self) -> None:
        """Remove oldest completed/failed tasks when over limit. Must hold lock."""
        completed = [
            t
            for t in self._tasks.values()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
        ]
        if len(completed) <= _MAX_COMPLETED_TASKS:
            return

        completed.sort(key=lambda t: t.created_at)
        to_evict = len(completed) - _MAX_COMPLETED_TASKS
        for task in completed[:to_evict]:
            del self._tasks[task.id]
