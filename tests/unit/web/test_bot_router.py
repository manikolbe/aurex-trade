"""Tests for the bot control API endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from aurex_trade.web._bot_sessions import BotSessionManager


class TestBotStatus:
    """Tests for GET /api/bot/status."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/bot/status")
        assert resp.status_code == 200

    def test_not_running_by_default(self, client: TestClient) -> None:
        data = client.get("/api/bot/status").json()
        assert data["running"] is False
        assert data["metrics"] is None


class TestBotStart:
    """Tests for POST /api/bot/start."""

    def test_missing_credentials_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/bot/start",
            json={
                "strategy_name": "ciby_sliding_grid",
                "strategy_params": {"grid_spacing": 10, "anchor_gap": 15},
                "risk_params": {},
                "symbol": "XAU_USD",
                "interval_seconds": 60,
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["running"] is False
        assert "credentials" in data["error"].lower() or "OANDA" in data["error"]

    def test_already_running_returns_409(self, client: TestClient) -> None:
        """Start when bot is already running returns 409."""
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="ciby_sliding_grid",
        )
        try:
            resp = client.post(
                "/api/bot/start",
                json={
                    "strategy_name": "ciby_sliding_grid",
                    "strategy_params": {},
                    "risk_params": {},
                    "symbol": "XAU_USD",
                    "interval_seconds": 60,
                },
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["running"] is True
            assert "already running" in data["error"].lower()
        finally:
            manager.stop("test-user-id")

    @patch("aurex_trade.web.routers.bot._common.create_bot_engine")
    def test_start_success(self, mock_factory: MagicMock, client: TestClient) -> None:
        """Successful start returns running status."""
        import threading

        # Make engine.run() block until we release it, so the session is
        # still alive when the endpoint builds the response.
        run_event = threading.Event()

        mock_engine = MagicMock()
        mock_engine.run.side_effect = lambda: run_event.wait(timeout=5)
        mock_engine.get_metrics.return_value = {
            "cycle_count": 0,
            "started_at": None,
            "running": True,
            "session_signals": 0,
            "session_trades": 0,
            "session_rejections": 0,
            "current_equity": 10000.0,
            "balance": 10000.0,
            "unrealized_pnl": 0.0,
            "open_position_count": 0,
            "peak_equity": 10000.0,
            "uptime_seconds": 0.0,
        }
        mock_connection = MagicMock()
        mock_factory.return_value = (mock_engine, mock_connection)

        resp = client.post(
            "/api/bot/start",
            json={
                "strategy_name": "ciby_sliding_grid",
                "strategy_params": {"grid_spacing": 10, "anchor_gap": 15},
                "risk_params": {},
                "symbol": "XAU_USD",
                "interval_seconds": 60,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["strategy_name"] == "ciby_sliding_grid"
        assert data["symbol"] == "XAU_USD"

        # Release the engine thread and cleanup
        run_event.set()
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        manager.stop("test-user-id")


class TestBotStop:
    """Tests for POST /api/bot/stop."""

    def test_returns_200(self, client: TestClient) -> None:
        resp = client.post("/api/bot/stop")
        assert resp.status_code == 200

    def test_stop_when_not_running(self, client: TestClient) -> None:
        data = client.post("/api/bot/stop").json()
        assert data["running"] is False

    def test_stop_clears_session(self, client: TestClient) -> None:
        """Stop removes the bot session."""
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="ciby_sliding_grid",
        )
        resp = client.post("/api/bot/stop")
        assert resp.json()["running"] is False
        assert not manager.is_running("test-user-id")


class TestBotMetrics:
    """Tests for GET /api/bot/metrics."""

    def test_not_running_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/bot/metrics")
        assert resp.status_code == 404

    def test_running_returns_metrics(self, client: TestClient) -> None:
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        engine.get_metrics.return_value = {
            "cycle_count": 5,
            "started_at": None,
            "running": True,
            "session_signals": 2,
            "session_trades": 1,
            "session_rejections": 1,
            "current_equity": 10500.0,
            "balance": 10500.0,
            "unrealized_pnl": 0.0,
            "open_position_count": 0,
            "peak_equity": 10500.0,
            "uptime_seconds": 300.0,
        }
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="ciby_sliding_grid",
        )
        try:
            resp = client.get("/api/bot/metrics")
            assert resp.status_code == 200
            data = resp.json()
            assert data["cycle_count"] == 5
            assert data["session_trades"] == 1
            assert data["peak_equity"] == 10500.0
        finally:
            manager.stop("test-user-id")


class TestBotEquity:
    """Tests for GET /api/bot/equity (charts + realized P&L card data)."""

    def test_not_running_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/bot/equity")
        assert resp.status_code == 404

    def test_running_returns_ledger_and_realized_total(
        self, client: TestClient
    ) -> None:
        manager: BotSessionManager = client.app.state.bot_session_manager  # type: ignore[union-attr]
        engine = MagicMock()
        engine.get_equity_history.return_value = []
        engine.get_trade_markers.return_value = []
        engine.get_event_log.return_value = []
        engine.get_session_history.return_value = []
        engine.get_realized_ledger.return_value = [
            {
                "timestamp": "2026-06-26T13:42:00+00:00",
                "kind": "trim",
                "realized_pnl": 8.4,
                "basis": "exact",
                "grid_level": "2418.00_long",
                "broker_trade_id": "10231",
            }
        ]
        engine.get_realized_pnl.return_value = 71.4
        connection = MagicMock()
        manager.start(
            user_id="test-user-id",
            engine=engine,
            connection=connection,
            symbol="XAU_USD",
            strategy_name="ciby_sliding_grid",
        )
        try:
            resp = client.get("/api/bot/equity")
            assert resp.status_code == 200
            data = resp.json()
            assert "realized_ledger" in data
            assert data["realized_ledger"][0]["kind"] == "trim"
            assert data["realized_pnl"] == 71.4
        finally:
            manager.stop("test-user-id")
