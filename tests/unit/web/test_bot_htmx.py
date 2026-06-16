"""Tests for the bot HTMX endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from aurex_trade.web._bot_sessions import BotSessionManager


class TestBotStatusPoll:
    """Tests for GET /htmx/bot/status/poll."""

    def test_idle_returns_html(self, client: TestClient) -> None:
        resp = client.get("/htmx/bot/status/poll")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Start Bot" in resp.text

    def test_running_returns_running_partial(self, client: TestClient) -> None:
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        engine.get_metrics.return_value = {
            "cycle_count": 3,
            "started_at": None,
            "running": True,
            "session_signals": 1,
            "session_trades": 0,
            "session_rejections": 1,
            "current_equity": 10000.0,
            "balance": 10000.0,
            "unrealized_pnl": 0.0,
            "open_position_count": 0,
            "peak_equity": 10000.0,
            "uptime_seconds": 60.0,
            "open_units": 0.0,
            "open_side": "flat",
            "realized_pnl": 0.0,
            "win_rate": None,
            "avg_slippage": None,
            "current_price": None,
        }
        engine.kill_switch = False
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="sma_crossover",
            granularity="M1",
            strategy_params={"short_window": 10, "long_window": 30},
            risk_params={"max_position_size": 10, "risk_per_trade": 0.02},
        )
        try:
            resp = client.get("/htmx/bot/status/poll")
            assert resp.status_code == 200
            assert "Running" in resp.text
            # Template renders the strategy name title-cased for display.
            assert "Sma Crossover" in resp.text
            assert "XAU_USD" in resp.text
        finally:
            manager.stop("test-user-id")


class TestBotStopHtmx:
    """Tests for POST /htmx/bot/stop."""

    def test_stop_redirects_to_bot_page(self, client: TestClient) -> None:
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="sma_crossover",
        )
        resp = client.post("/htmx/bot/stop", follow_redirects=False)
        assert resp.status_code == 200
        assert resp.headers["hx-redirect"] == "/bot"
        assert not manager.is_running("test-user-id")


class TestBotMetricsPoll:
    """Tests for GET /htmx/bot/metrics/poll."""

    def test_not_running_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/htmx/bot/metrics/poll")
        assert resp.status_code == 200
        assert resp.text == ""

    def test_running_returns_metrics_partial(self, client: TestClient) -> None:
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        engine.get_metrics.return_value = {
            "cycle_count": 10,
            "started_at": None,
            "running": True,
            "session_signals": 5,
            "session_trades": 2,
            "session_rejections": 3,
            "current_equity": 11000.0,
            "balance": 11000.0,
            "unrealized_pnl": 0.0,
            "open_position_count": 0,
            "peak_equity": 11000.0,
            "uptime_seconds": 600.0,
            "open_units": 5.0,
            "open_side": "long",
            "realized_pnl": 50.0,
            "win_rate": 0.5,
            "avg_slippage": 0.3,
            "current_price": 2050.0,
        }
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="rsi_mean_reversion",
        )
        try:
            resp = client.get("/htmx/bot/metrics/poll")
            assert resp.status_code == 200
            assert "10" in resp.text  # cycle_count
            assert "11000" in resp.text  # peak_equity
        finally:
            manager.stop("test-user-id")
