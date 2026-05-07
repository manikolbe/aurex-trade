"""Tests for the background TaskRegistry."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from aurex_trade.web.tasks import TaskInfo, TaskRegistry, TaskStatus


class TestSubmit:
    """Tests for task submission."""

    def test_submit_returns_uuid(self) -> None:
        """Submit returns a UUID task ID."""
        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(lambda: "done", task_type="test")
            assert task_id is not None
        finally:
            registry.shutdown()

    def test_submitted_task_becomes_running(self) -> None:
        """Task transitions to RUNNING after submit."""
        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(lambda: time.sleep(1), task_type="test")
            info = registry.get(task_id)
            assert info is not None
            assert info.status == TaskStatus.RUNNING
            assert info.task_type == "test"
        finally:
            registry.shutdown()

    def test_submitted_task_has_created_at(self) -> None:
        """Task has a UTC created_at timestamp."""
        registry = TaskRegistry(max_workers=1)
        before = datetime.now(UTC)
        try:
            task_id = registry.submit(lambda: time.sleep(1), task_type="test")
            info = registry.get(task_id)
            assert info is not None
            assert info.created_at >= before
            assert info.created_at.tzinfo is not None
        finally:
            registry.shutdown()


def _wait_for_terminal(registry: TaskRegistry, task_id, timeout: float = 5.0) -> TaskInfo:
    """Poll until task reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = registry.get(task_id)
        if info and info.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return info
        time.sleep(0.01)
    info = registry.get(task_id)
    status = info.status if info else "NOT FOUND"
    msg = f"Task did not reach terminal state (current: {status})"
    raise TimeoutError(msg)


class TestCompletion:
    """Tests for task completion and failure."""

    def test_successful_task_stores_result(self) -> None:
        """Completed task has COMPLETED status and result stored."""
        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(lambda: {"answer": 42}, task_type="test")
            info = _wait_for_terminal(registry, task_id)
            assert info.status == TaskStatus.COMPLETED
            assert info.result == {"answer": 42}
            assert info.completed_at is not None
            assert info.error is None
        finally:
            registry.shutdown()

    def test_failed_task_stores_error(self) -> None:
        """Failed task has FAILED status and error message."""

        def failing():
            raise FileNotFoundError("No data found")

        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(failing, task_type="test")
            info = _wait_for_terminal(registry, task_id)
            assert info.status == TaskStatus.FAILED
            assert info.result is None
            assert info.error == "FileNotFoundError: No data found"
            assert info.completed_at is not None
        finally:
            registry.shutdown()

    def test_none_result_is_valid(self) -> None:
        """Task returning None still completes successfully."""
        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(lambda: None, task_type="test")
            info = _wait_for_terminal(registry, task_id)
            assert info.status == TaskStatus.COMPLETED
            assert info.result is None
            assert info.error is None
        finally:
            registry.shutdown()


class TestGet:
    """Tests for task retrieval."""

    def test_get_unknown_returns_none(self) -> None:
        """Getting a non-existent task returns None."""
        registry = TaskRegistry(max_workers=1)
        try:
            assert registry.get(uuid4()) is None
        finally:
            registry.shutdown()


class TestListAll:
    """Tests for listing all tasks."""

    def test_list_all_empty(self) -> None:
        """Empty registry returns empty list."""
        registry = TaskRegistry(max_workers=1)
        try:
            assert registry.list_all() == []
        finally:
            registry.shutdown()

    def test_list_all_ordered_by_most_recent(self) -> None:
        """Tasks are returned most recent first."""
        registry = TaskRegistry(max_workers=1)
        try:
            id1 = registry.submit(lambda: 1, task_type="first")
            time.sleep(0.01)
            id2 = registry.submit(lambda: 2, task_type="second")
            registry.shutdown()
            tasks = registry.list_all()
            assert len(tasks) == 2
            assert tasks[0].id == id2
            assert tasks[1].id == id1
        finally:
            pass


class TestEviction:
    """Tests for automatic eviction of old completed tasks."""

    def test_evicts_oldest_when_over_limit(self) -> None:
        """Old completed tasks are evicted when count exceeds 100."""
        registry = TaskRegistry(max_workers=4)
        try:
            # Submit 105 tasks that complete immediately
            ids = []
            for _i in range(105):
                task_id = registry.submit(lambda: "done", task_type="test")
                ids.append(task_id)
            registry.shutdown()

            # After eviction, should have at most 100 completed tasks
            all_tasks = registry.list_all()
            completed = [t for t in all_tasks if t.status == TaskStatus.COMPLETED]
            assert len(completed) <= 100

            # The most recent tasks should still be accessible
            assert registry.get(ids[-1]) is not None
        finally:
            pass


class TestUpdateMessage:
    """Tests for task message updates."""

    def test_update_message_sets_message(self) -> None:
        """update_message sets the message field on a running task."""
        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(lambda: time.sleep(1), task_type="test")
            registry.update_message(task_id, "Downloading data...")
            info = registry.get(task_id)
            assert info is not None
            assert info.message == "Downloading data..."
        finally:
            registry.shutdown()

    def test_update_message_unknown_task_is_noop(self) -> None:
        """update_message on unknown task_id does nothing."""
        registry = TaskRegistry(max_workers=1)
        try:
            registry.update_message(uuid4(), "should not crash")
        finally:
            registry.shutdown()


class TestSubmitWithTaskId:
    """Tests for submitting with a pre-generated task_id."""

    def test_submit_uses_provided_task_id(self) -> None:
        """submit() uses the given task_id instead of generating one."""
        registry = TaskRegistry(max_workers=1)
        try:
            expected_id = uuid4()
            returned_id = registry.submit(
                lambda: "done", task_type="test", task_id=expected_id
            )
            assert returned_id == expected_id
            info = registry.get(expected_id)
            assert info is not None
            assert info.id == expected_id
        finally:
            registry.shutdown()

    def test_submit_generates_id_when_none(self) -> None:
        """submit() generates a UUID when task_id is None."""
        registry = TaskRegistry(max_workers=1)
        try:
            task_id = registry.submit(lambda: "done", task_type="test", task_id=None)
            assert task_id is not None
            info = registry.get(task_id)
            assert info is not None
        finally:
            registry.shutdown()


class TestConcurrency:
    """Tests for thread-safety."""

    def test_concurrent_submissions(self) -> None:
        """Multiple threads submitting simultaneously don't corrupt state."""
        registry = TaskRegistry(max_workers=4)
        ids: list = []
        lock = threading.Lock()

        def submit_task():
            task_id = registry.submit(lambda: "ok", task_type="concurrent")
            with lock:
                ids.append(task_id)

        threads = [threading.Thread(target=submit_task) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        registry.shutdown()

        assert len(ids) == 20
        # All tasks should be retrievable and reach terminal state
        for task_id in ids:
            info = _wait_for_terminal(registry, task_id)
            assert info.status == TaskStatus.COMPLETED
