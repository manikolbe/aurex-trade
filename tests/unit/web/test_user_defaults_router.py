"""Tests for user defaults API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestStrategyDefaultsEndpoints:
    def test_get_strategy_defaults_returns_valid_structure(self, client: TestClient) -> None:
        resp = client.get("/api/user-defaults/strategy")
        assert resp.status_code == 200
        data = resp.json()
        assert "preferred_strategy" in data
        assert "strategies" in data
        assert isinstance(data["strategies"], dict)

    def test_put_and_get_strategy_defaults(self, client: TestClient) -> None:
        resp = client.put(
            "/api/user-defaults/strategy/sma_crossover",
            json={"params": {"short_window": 15, "long_window": 40}, "is_preferred": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["preferred_strategy"] == "sma_crossover"
        assert data["strategies"]["sma_crossover"] == {"short_window": 15, "long_window": 40}

        # Verify GET returns the same
        resp = client.get("/api/user-defaults/strategy")
        data = resp.json()
        assert data["preferred_strategy"] == "sma_crossover"
        assert data["strategies"]["sma_crossover"] == {"short_window": 15, "long_window": 40}

    def test_put_preferred_clears_old(self, client: TestClient) -> None:
        client.put(
            "/api/user-defaults/strategy/sma_crossover",
            json={"params": {"short_window": 10}, "is_preferred": True},
        )
        client.put(
            "/api/user-defaults/strategy/rsi_mean_reversion",
            json={"params": {"period": 14}, "is_preferred": True},
        )
        data = client.get("/api/user-defaults/strategy").json()
        assert data["preferred_strategy"] == "rsi_mean_reversion"

    def test_delete_strategy_defaults(self, client: TestClient) -> None:
        client.put(
            "/api/user-defaults/strategy/sma_crossover",
            json={"params": {"short_window": 15}, "is_preferred": False},
        )
        resp = client.delete("/api/user-defaults/strategy/sma_crossover")
        assert resp.status_code == 204

        data = client.get("/api/user-defaults/strategy").json()
        assert "sma_crossover" not in data["strategies"]


class TestRiskDefaultsEndpoints:
    def test_get_risk_defaults_returns_valid_structure(self, client: TestClient) -> None:
        resp = client.get("/api/user-defaults/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert "settings" in data

    def test_put_and_get_risk_defaults(self, client: TestClient) -> None:
        settings = {
            "max_position": 20,
            "max_daily_loss": 1000.0,
            "risk_per_trade": 0.03,
            "max_drawdown_pct": 0.15,
            "max_trades_per_day": 50,
            "max_consecutive_losses": 3,
            "require_stop_loss": True,
            "capital": 200000.0,
            "position_size": 2.0,
            "spread": 0.8,
            "slippage": 0.3,
            "commission": 1.0,
            "seed": 123,
        }
        resp = client.put("/api/user-defaults/risk", json=settings)
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings"]["max_position"] == 20
        assert data["settings"]["capital"] == 200000.0

        # Verify GET returns saved values
        data = client.get("/api/user-defaults/risk").json()
        assert data["settings"]["max_position"] == 20

    def test_delete_risk_defaults(self, client: TestClient) -> None:
        client.put(
            "/api/user-defaults/risk",
            json={
                "max_position": 10,
                "max_daily_loss": 500,
                "risk_per_trade": 0.02,
                "max_drawdown_pct": 0.2,
                "max_trades_per_day": 100,
                "max_consecutive_losses": 5,
                "require_stop_loss": True,
                "capital": 100000,
                "position_size": 1.0,
                "spread": 0.6,
                "slippage": 0.2,
                "commission": 0.0,
                "seed": 42,
            },
        )
        resp = client.delete("/api/user-defaults/risk")
        assert resp.status_code == 204

        data = client.get("/api/user-defaults/risk").json()
        assert data["settings"] is None


class TestAllDefaultsEndpoint:
    def test_get_all_defaults_returns_valid_structure(self, client: TestClient) -> None:
        resp = client.get("/api/user-defaults/all")
        assert resp.status_code == 200
        data = resp.json()
        assert "preferred_strategy" in data
        assert "strategy_params" in data
        assert "risk_settings" in data
        assert isinstance(data["strategy_params"], dict)

    def test_get_all_defaults_combined(self, client: TestClient) -> None:
        client.put(
            "/api/user-defaults/strategy/sma_crossover",
            json={"params": {"short_window": 15}, "is_preferred": True},
        )
        client.put(
            "/api/user-defaults/risk",
            json={
                "max_position": 20,
                "max_daily_loss": 1000,
                "risk_per_trade": 0.02,
                "max_drawdown_pct": 0.2,
                "max_trades_per_day": 100,
                "max_consecutive_losses": 5,
                "require_stop_loss": True,
                "capital": 100000,
                "position_size": 1.0,
                "spread": 0.6,
                "slippage": 0.2,
                "commission": 0.0,
                "seed": 42,
            },
        )

        data = client.get("/api/user-defaults/all").json()
        assert data["preferred_strategy"] == "sma_crossover"
        assert data["strategy_params"]["sma_crossover"] == {"short_window": 15}
        assert data["risk_settings"]["max_position"] == 20


class TestAuthRequired:
    def test_unauthenticated_request_rejected(self) -> None:
        from aurex_trade.web.app import create_app

        app = create_app()
        with TestClient(app, follow_redirects=False) as c:
            resp = c.get("/api/user-defaults/all")
            assert resp.status_code in (401, 307)
