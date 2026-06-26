"""Tests for Pydantic request/response schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aurex_trade.web.schemas import BacktestRequest, SweepRequest, WalkForwardRequest


class TestBacktestRequestValidation:
    """Tests for BacktestRequest field validation."""

    def test_defaults_are_valid(self) -> None:
        """Request with all defaults is valid."""
        req = BacktestRequest()
        assert req.symbol == "XAU_USD"
        assert req.granularity == "M1"
        assert req.capital == 100_000.0
        assert req.strategy == "ciby_sliding_grid"
        assert req.params == {}

    def test_strategy_and_params(self) -> None:
        """Strategy and params fields are accepted."""
        req = BacktestRequest(
            strategy="ciby_hedged_doubling_grid",
            params={"spacing": 20, "units": 2, "trailing_stop_distance": 20},
        )
        assert req.strategy == "ciby_hedged_doubling_grid"
        assert req.params["spacing"] == 20

    def test_valid_date_format(self) -> None:
        """YYYY-MM-DD dates are accepted."""
        req = BacktestRequest(start_date="2025-01-15", end_date="2025-01-20")
        assert req.start_date == "2025-01-15"
        assert req.end_date == "2025-01-20"

    def test_empty_dates_are_valid(self) -> None:
        """Empty string dates are accepted (means 'all data')."""
        req = BacktestRequest(start_date="", end_date="")
        assert req.start_date == ""
        assert req.end_date == ""

    def test_invalid_date_format_rejected(self) -> None:
        """Non YYYY-MM-DD date format raises ValidationError."""
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            BacktestRequest(start_date="15/01/2025")

    def test_invalid_date_partial_rejected(self) -> None:
        """Incomplete date format is rejected."""
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            BacktestRequest(start_date="2025-1-1")

    def test_invalid_symbol_lowercase(self) -> None:
        """Lowercase symbol is rejected."""
        with pytest.raises(ValidationError):
            BacktestRequest(symbol="xau_usd")

    def test_invalid_symbol_too_long(self) -> None:
        """Symbol longer than 20 chars is rejected."""
        with pytest.raises(ValidationError):
            BacktestRequest(symbol="A" * 21)

    def test_invalid_symbol_special_chars(self) -> None:
        """Symbol with special characters is rejected."""
        with pytest.raises(ValidationError):
            BacktestRequest(symbol="XAU/USD")

    def test_valid_symbol_with_underscore(self) -> None:
        """Symbol with underscore is valid."""
        req = BacktestRequest(symbol="EUR_USD")
        assert req.symbol == "EUR_USD"

    def test_invalid_granularity(self) -> None:
        """Unknown granularity is rejected."""
        with pytest.raises(ValidationError, match="Unknown granularity"):
            BacktestRequest(granularity="X1")

    def test_valid_granularities(self) -> None:
        """All known granularity values are accepted."""
        for g in ("M1", "M5", "M15", "H1", "H4", "D", "W"):
            req = BacktestRequest(granularity=g)
            assert req.granularity == g

    def test_zero_capital_rejected(self) -> None:
        """Capital must be greater than 0."""
        with pytest.raises(ValidationError):
            BacktestRequest(capital=0)

    def test_negative_capital_rejected(self) -> None:
        """Negative capital is rejected."""
        with pytest.raises(ValidationError):
            BacktestRequest(capital=-1000)

    def test_zero_spread_allowed(self) -> None:
        """Zero spread is valid (ge=0)."""
        req = BacktestRequest(spread=0.0)
        assert req.spread == 0.0

    def test_negative_spread_rejected(self) -> None:
        """Negative spread is rejected."""
        with pytest.raises(ValidationError):
            BacktestRequest(spread=-0.1)

    def test_risk_per_trade_bounds(self) -> None:
        """Risk per trade must be >0 and <=1."""
        with pytest.raises(ValidationError):
            BacktestRequest(risk_per_trade=0.0)
        with pytest.raises(ValidationError):
            BacktestRequest(risk_per_trade=1.1)
        req = BacktestRequest(risk_per_trade=1.0)
        assert req.risk_per_trade == 1.0

    def test_max_drawdown_pct_bounds(self) -> None:
        """Max drawdown pct must be >0 and <=1."""
        with pytest.raises(ValidationError):
            BacktestRequest(max_drawdown_pct=0.0)
        with pytest.raises(ValidationError):
            BacktestRequest(max_drawdown_pct=1.5)
        req = BacktestRequest(max_drawdown_pct=1.0)
        assert req.max_drawdown_pct == 1.0


class TestSweepRequestValidation:
    """Tests for SweepRequest parameter grid validation."""

    def test_valid_params(self) -> None:
        """Normal parameter grid is accepted."""
        req = SweepRequest(params={"grid_spacing": [5, 10], "anchor_gap": [15, 20]})
        assert req.params == {"grid_spacing": [5, 10], "anchor_gap": [15, 20]}

    def test_ranking_defaults(self) -> None:
        """Web defaults match the engine: rank by total_pnl with a 30-trade floor."""
        sweep = SweepRequest(params={"grid_spacing": [5, 10]})
        wf = WalkForwardRequest(params={"grid_spacing": [5, 10]})
        assert sweep.rank_by == "total_pnl"
        assert sweep.min_trades == 30
        assert wf.rank_by == "total_pnl"
        assert wf.min_trades == 30

    def test_too_many_values_per_param(self) -> None:
        """More than 50 values per parameter list is rejected."""
        with pytest.raises(ValidationError, match="at most 50"):
            SweepRequest(params={"grid_spacing": list(range(51))})

    def test_too_many_combinations(self) -> None:
        """Total combinations exceeding 1000 is rejected."""
        # 50 * 50 * 2 = 5000 > 1000 (3 params each ≤50 but total too high)
        with pytest.raises(ValidationError, match="exceeds limit"):
            SweepRequest(
                params={
                    "grid_spacing": list(range(1, 51)),  # 50 values
                    "anchor_gap": list(range(1, 51)),  # 50 values
                    "extra": [1, 2],  # 2 values → 50*50*2 = 5000
                }
            )

    def test_exactly_1000_combinations_allowed(self) -> None:
        """Exactly 1000 combinations is valid."""
        # 50 * 20 = 1000
        req = SweepRequest(
            params={
                "grid_spacing": list(range(1, 51)),  # 50 values
                "anchor_gap": list(range(1, 21)),  # 20 values
            }
        )
        assert len(req.params["grid_spacing"]) == 50

    def test_too_many_param_keys(self) -> None:
        """More than 10 parameter keys is rejected."""
        params = {f"param_{i}": [1, 2] for i in range(11)}
        with pytest.raises(ValidationError):
            SweepRequest(params=params)

    def test_empty_params_rejected(self) -> None:
        """Params field is required."""
        with pytest.raises(ValidationError):
            SweepRequest()  # type: ignore[call-arg]

    def test_inherits_date_validation(self) -> None:
        """SweepRequest uses the same date validation."""
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            SweepRequest(
                params={"grid_spacing": [5, 10]},
                start_date="not-a-date",
            )

    def test_inherits_granularity_validation(self) -> None:
        """SweepRequest uses the same granularity validation."""
        with pytest.raises(ValidationError, match="Unknown granularity"):
            SweepRequest(
                params={"grid_spacing": [5, 10]},
                granularity="XX",
            )


class TestWalkForwardRequestValidation:
    """Tests for WalkForwardRequest validation."""

    def test_valid_request(self) -> None:
        """Normal walk-forward request is accepted."""
        req = WalkForwardRequest(
            params={"grid_spacing": [5, 10], "anchor_gap": [20, 30]},
            train_bars=5000,
            test_bars=2000,
        )
        assert req.train_bars == 5000
        assert req.test_bars == 2000

    def test_zero_train_bars_rejected(self) -> None:
        """Train bars must be > 0."""
        with pytest.raises(ValidationError):
            WalkForwardRequest(
                params={"grid_spacing": [5, 10]},
                train_bars=0,
            )

    def test_zero_test_bars_rejected(self) -> None:
        """Test bars must be > 0."""
        with pytest.raises(ValidationError):
            WalkForwardRequest(
                params={"grid_spacing": [5, 10]},
                test_bars=0,
            )

    def test_inherits_param_validation(self) -> None:
        """WalkForwardRequest uses the same param grid limits."""
        with pytest.raises(ValidationError, match="at most 50"):
            WalkForwardRequest(params={"grid_spacing": list(range(51))})


class TestBacktestResultConverter:
    """The web converter must report the same trade count as the CLI."""

    def test_trade_count_uses_metrics_not_records(self) -> None:
        """Web badge reads metrics.trade_count, not len(trades).

        Grid limit/stop fills and the terminal flatten add to realized P&L
        (metrics.trade_count) without producing BacktestTradeRecords, so the
        record list under-counts. The web response must match the CLI.
        """
        from aurex_trade.backtest.results import BacktestResult
        from aurex_trade.metrics import calculate_metrics
        from aurex_trade.web.schemas import backtest_result_to_response

        # 5 realized trades, but no trade records attached (the grid case).
        metrics = calculate_metrics(
            equity_curve=[100_000.0, 100_050.0],
            trade_pnls=[10.0, -5.0, 20.0, -3.0, 8.0],
            initial_capital=100_000.0,
        )
        result = BacktestResult(metrics=metrics, trades=[])
        response = backtest_result_to_response(result)

        assert response.trade_count == 5
        assert response.trade_count == metrics.trade_count
