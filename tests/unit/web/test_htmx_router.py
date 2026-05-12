"""Tests for the HTMX router endpoints."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient


class TestHtmxBacktest:
    """Tests for HTMX backtest submit and poll endpoints."""

    def test_submit_returns_loading_html(self, client: TestClient) -> None:
        """POST /htmx/backtest/submit returns HTML with polling trigger."""
        with patch("aurex_trade.web.routers.backtest.htmx.create_backtest_runner") as mock:
            mock.return_value = lambda: None
            response = client.post(
                "/htmx/backtest/submit",
                json={
                    "symbol": "XAU_USD",
                    "strategy": "sma_crossover",
                    "params": {"short_window": 10, "long_window": 30},
                },
            )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "hx-get" in response.text
        assert "hx-trigger" in response.text
        assert "Running backtest" in response.text

    def test_poll_not_found(self, client: TestClient) -> None:
        """GET /htmx/backtest/{task_id}/poll returns error for unknown task."""
        task_id = uuid4()
        response = client.get(f"/htmx/backtest/{task_id}/poll")
        assert response.status_code == 200
        assert "Task not found" in response.text
        assert "alert-error" in response.text

    def test_poll_running_returns_loading(self, client: TestClient) -> None:
        """Poll returns loading HTML when task is still running."""
        with patch("aurex_trade.web.routers.backtest.htmx.create_backtest_runner") as mock:
            mock.return_value = lambda: time.sleep(10)
            submit_resp = client.post(
                "/htmx/backtest/submit",
                json={
                    "symbol": "XAU_USD",
                    "strategy": "sma_crossover",
                    "params": {"short_window": 10, "long_window": 30},
                },
            )

        match = re.search(r"/htmx/backtest/([a-f0-9-]+)/poll", submit_resp.text)
        assert match is not None
        task_id = match.group(1)

        poll_resp = client.get(f"/htmx/backtest/{task_id}/poll")
        assert poll_resp.status_code == 200
        assert "Running backtest" in poll_resp.text

    def test_poll_failed_returns_error(self, client: TestClient) -> None:
        """Poll returns error HTML when task has failed."""
        from aurex_trade.web.tasks import TaskInfo, TaskStatus

        # Directly inject a failed task into the registry
        task_id = uuid4()
        registry = client.app.state.task_registry  # type: ignore[union-attr]
        failed_info = TaskInfo(
            id=task_id,
            task_type="backtest",
            status=TaskStatus.FAILED,
            created_at=datetime.now(UTC),
            error="FileNotFoundError: No data found",
        )
        with registry._lock:
            registry._tasks[task_id] = failed_info

        poll_resp = client.get(f"/htmx/backtest/{task_id}/poll")
        assert poll_resp.status_code == 200
        assert "alert-error" in poll_resp.text
        assert "No data" in poll_resp.text


class TestHtmxSweep:
    """Tests for HTMX sweep submit and poll endpoints."""

    def test_submit_returns_loading_html(self, client: TestClient) -> None:
        """POST /htmx/sweep/submit returns HTML with polling trigger."""
        with patch("aurex_trade.web.routers.backtest.htmx.create_sweep_runner") as mock:
            mock.return_value = lambda: None
            response = client.post(
                "/htmx/sweep/submit",
                json={
                    "strategy": "sma_crossover",
                    "params": {"short_window": [5, 10], "long_window": [20, 30]},
                },
            )
        assert response.status_code == 200
        assert "Running parameter sweep" in response.text
        assert "hx-get" in response.text

    def test_poll_not_found(self, client: TestClient) -> None:
        """GET /htmx/sweep/{task_id}/poll returns error for unknown task."""
        task_id = uuid4()
        response = client.get(f"/htmx/sweep/{task_id}/poll")
        assert response.status_code == 200
        assert "Task not found" in response.text


class TestHtmxWalkForward:
    """Tests for HTMX walk-forward submit and poll endpoints."""

    def test_submit_returns_loading_html(self, client: TestClient) -> None:
        """POST /htmx/walk-forward/submit returns HTML with polling trigger."""
        with patch("aurex_trade.web.routers.backtest.htmx.create_walk_forward_runner") as mock:
            mock.return_value = lambda: None
            response = client.post(
                "/htmx/walk-forward/submit",
                json={
                    "strategy": "sma_crossover",
                    "params": {"short_window": [5, 10], "long_window": [20, 30]},
                    "train_bars": 100,
                    "test_bars": 100,
                },
            )
        assert response.status_code == 200
        assert "Running walk-forward" in response.text
        assert "hx-get" in response.text

    def test_poll_not_found(self, client: TestClient) -> None:
        """GET /htmx/walk-forward/{task_id}/poll returns error for unknown task."""
        task_id = uuid4()
        response = client.get(f"/htmx/walk-forward/{task_id}/poll")
        assert response.status_code == 200
        assert "Task not found" in response.text


class TestDownsampleCurve:
    """Tests for the equity curve downsampling function."""

    def test_short_curve_unchanged(self) -> None:
        """Curves shorter than max_points are returned unchanged."""
        from aurex_trade.web.schemas import _downsample_curve

        curve = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _downsample_curve(curve, max_points=500) == curve

    def test_long_curve_downsampled(self) -> None:
        """Curves longer than max_points are downsampled."""
        from aurex_trade.web.schemas import _downsample_curve

        curve = [float(x) for x in range(1000)]
        result = _downsample_curve(curve, max_points=100)
        assert len(result) == 100
        assert result[0] == 0.0  # first preserved
        assert result[-1] == 999.0  # last preserved

    def test_exact_max_points(self) -> None:
        """Curve of exactly max_points is unchanged."""
        from aurex_trade.web.schemas import _downsample_curve

        curve = [float(x) for x in range(500)]
        assert _downsample_curve(curve, max_points=500) == curve
