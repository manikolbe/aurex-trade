"""Tests for the backtest/sweep/walk-forward API router."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient


class TestBacktestSubmit:
    """Tests for POST /api/backtest."""

    def test_returns_202(self, client: TestClient) -> None:
        """Submit returns 202 Accepted."""
        with patch("aurex_trade.web.routers.backtest.api.create_backtest_runner") as mock:
            mock.return_value = lambda: None
            resp = client.post(
                "/api/backtest",
                json={
                    "symbol": "XAU_USD",
                    "strategy": "sma_crossover",
                    "params": {"short_window": 10, "long_window": 30},
                },
            )
        assert resp.status_code == 202

    def test_response_shape(self, client: TestClient) -> None:
        """Submit returns task_id, task_type, and status."""
        with patch("aurex_trade.web.routers.backtest.api.create_backtest_runner") as mock:
            mock.return_value = lambda: None
            data = client.post(
                "/api/backtest",
                json={
                    "symbol": "XAU_USD",
                    "strategy": "sma_crossover",
                    "params": {"short_window": 10, "long_window": 30},
                },
            ).json()
        assert "task_id" in data
        assert data["task_type"] == "backtest"
        assert data["status"] == "running"

    def test_rsi_strategy_accepted(self, client: TestClient) -> None:
        """RSI strategy with params returns 202."""
        with patch("aurex_trade.web.routers.backtest.api.create_backtest_runner") as mock:
            mock.return_value = lambda: None
            resp = client.post(
                "/api/backtest",
                json={
                    "strategy": "rsi_mean_reversion",
                    "params": {"period": 14, "overbought": 70, "oversold": 30},
                },
            )
        assert resp.status_code == 202

    def test_invalid_symbol_returns_422(self, client: TestClient) -> None:
        """Invalid symbol returns validation error."""
        resp = client.post("/api/backtest", json={"symbol": "invalid!"})
        assert resp.status_code == 422

    def test_invalid_date_returns_422(self, client: TestClient) -> None:
        """Invalid date format returns validation error."""
        resp = client.post("/api/backtest", json={"start_date": "not-a-date"})
        assert resp.status_code == 422

    def test_invalid_granularity_returns_422(self, client: TestClient) -> None:
        """Unknown granularity returns validation error."""
        resp = client.post("/api/backtest", json={"granularity": "X9"})
        assert resp.status_code == 422


class TestBacktestPoll:
    """Tests for GET /api/backtest/{task_id}."""

    def test_unknown_task_returns_404(self, client: TestClient) -> None:
        """Polling unknown task_id returns 404."""
        resp = client.get(f"/api/backtest/{uuid4()}")
        assert resp.status_code == 404
        assert resp.json()["error"] == "Task not found"

    def test_running_task_returns_status(self, client: TestClient) -> None:
        """Polling a running task returns TaskStatusResponse."""
        import time

        with patch("aurex_trade.web.routers.backtest.api.create_backtest_runner") as mock:
            mock.return_value = lambda: time.sleep(10)
            submit = client.post(
                "/api/backtest",
                json={"strategy": "sma_crossover", "params": {}},
            ).json()

        resp = client.get(f"/api/backtest/{submit['task_id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "task_type" in data

    def test_failed_task_returns_error(self, client: TestClient) -> None:
        """Polling a failed task returns status with error field."""
        from datetime import UTC, datetime

        from aurex_trade.web.tasks import TaskInfo, TaskStatus

        task_id = uuid4()
        registry = client.app.state.task_registry  # type: ignore[union-attr]
        failed_info = TaskInfo(
            id=task_id,
            task_type="backtest",
            status=TaskStatus.FAILED,
            created_at=datetime.now(UTC),
            error="ValueError: bad params",
        )
        with registry._lock:
            registry._tasks[task_id] = failed_info

        resp = client.get(f"/api/backtest/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error"] == "ValueError: bad params"


class TestSweepSubmit:
    """Tests for POST /api/sweep."""

    def test_returns_202(self, client: TestClient) -> None:
        """Submit returns 202 Accepted."""
        with patch("aurex_trade.web.routers.backtest.api.create_sweep_runner") as mock:
            mock.return_value = lambda: None
            resp = client.post(
                "/api/sweep",
                json={
                    "strategy": "sma_crossover",
                    "params": {"short_window": [5, 10], "long_window": [20, 30]},
                },
            )
        assert resp.status_code == 202
        assert resp.json()["task_type"] == "sweep"

    def test_oversized_params_returns_422(self, client: TestClient) -> None:
        """Params exceeding combination limit returns 422."""
        resp = client.post(
            "/api/sweep",
            json={
                "strategy": "sma_crossover",
                "params": {
                    "short_window": list(range(1, 52)),
                    "long_window": list(range(1, 22)),
                },
            },
        )
        assert resp.status_code == 422


class TestSweepPoll:
    """Tests for GET /api/sweep/{task_id}."""

    def test_unknown_task_returns_404(self, client: TestClient) -> None:
        """Polling unknown task_id returns 404."""
        resp = client.get(f"/api/sweep/{uuid4()}")
        assert resp.status_code == 404


class TestWalkForwardSubmit:
    """Tests for POST /api/walk-forward."""

    def test_returns_202(self, client: TestClient) -> None:
        """Submit returns 202 Accepted."""
        with patch("aurex_trade.web.routers.backtest.api.create_walk_forward_runner") as mock:
            mock.return_value = lambda: None
            resp = client.post(
                "/api/walk-forward",
                json={
                    "strategy": "sma_crossover",
                    "params": {"short_window": [5, 10], "long_window": [20, 30]},
                    "train_bars": 100,
                    "test_bars": 100,
                },
            )
        assert resp.status_code == 202
        assert resp.json()["task_type"] == "walk_forward"

    def test_zero_train_bars_returns_422(self, client: TestClient) -> None:
        """Zero train_bars returns validation error."""
        resp = client.post(
            "/api/walk-forward",
            json={
                "strategy": "sma_crossover",
                "params": {"short_window": [5, 10]},
                "train_bars": 0,
                "test_bars": 100,
            },
        )
        assert resp.status_code == 422


class TestWalkForwardPoll:
    """Tests for GET /api/walk-forward/{task_id}."""

    def test_unknown_task_returns_404(self, client: TestClient) -> None:
        """Polling unknown task_id returns 404."""
        resp = client.get(f"/api/walk-forward/{uuid4()}")
        assert resp.status_code == 404


class TestStrategiesEndpoint:
    """Tests for GET /api/strategies."""

    def test_returns_all_strategies(self, client: TestClient) -> None:
        """Returns both registered strategies."""
        resp = client.get("/api/strategies")
        assert resp.status_code == 200
        data = resp.json()
        names = [s["name"] for s in data["strategies"]]
        assert "sma_crossover" in names
        assert "rsi_mean_reversion" in names

    def test_response_includes_params_metadata(self, client: TestClient) -> None:
        """Each strategy includes params with full metadata."""
        resp = client.get("/api/strategies")
        data = resp.json()
        for strategy in data["strategies"]:
            assert len(strategy["params"]) > 0
            for param in strategy["params"]:
                assert "key" in param
                assert "label" in param
                assert "tooltip" in param
                assert "default" in param
                assert "min_value" in param
                assert "max_value" in param

    def test_sma_has_expected_params(self, client: TestClient) -> None:
        """SMA Crossover has short_window and long_window params."""
        resp = client.get("/api/strategies")
        data = resp.json()
        sma = next(s for s in data["strategies"] if s["name"] == "sma_crossover")
        param_keys = [p["key"] for p in sma["params"]]
        assert "short_window" in param_keys
        assert "long_window" in param_keys

    def test_rsi_has_expected_params(self, client: TestClient) -> None:
        """RSI Mean Reversion has period, overbought, oversold params."""
        resp = client.get("/api/strategies")
        data = resp.json()
        rsi = next(s for s in data["strategies"] if s["name"] == "rsi_mean_reversion")
        param_keys = [p["key"] for p in rsi["params"]]
        assert "period" in param_keys
        assert "overbought" in param_keys
        assert "oversold" in param_keys
