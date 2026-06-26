"""Tests for WalkForwardValidator — window splitting, isolation, aggregation."""

from datetime import UTC, datetime, timedelta

import pytest

from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.walk_forward import WalkForwardValidator
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from tests.conftest import StatelessTestStrategy


def _make_trending_bars(count: int) -> list[BarData]:
    """Generate bars with multiple up/down cycles to trigger crossover signals.

    Each cycle is 50 bars: 25 up, 25 down. Amplitude increases each cycle
    so that different time windows produce different P&L results.
    """
    bars = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    cycle_len = 50

    for i in range(count):
        pos_in_cycle = i % cycle_len
        cycle_num = i // cycle_len
        base = 100.0 + cycle_num * 2.0  # Larger drift per cycle
        amplitude = 0.3 + cycle_num * 0.15  # Increasing amplitude

        if pos_in_cycle < cycle_len // 2:
            price = base + pos_in_cycle * amplitude
        else:
            half = cycle_len // 2
            price = base + half * amplitude - (pos_in_cycle - half) * amplitude

        bars.append(
            BarData(
                timestamp=start + timedelta(minutes=i),
                open=price - 0.1,
                high=price + 0.2,
                low=price - 0.2,
                close=price,
                volume=1000.0,
                symbol="TEST",
            )
        )
    return bars


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbol="TEST",
        initial_capital=100_000.0,
        position_size=1.0,
        spread_pips=0.0,
        slippage_pips=0.0,
        commission_per_trade=0.0,
        deterministic_seed=42,
    )


def _risk_engine() -> RiskEngine:
    return RiskEngine(
        max_position_size=100,
        max_daily_loss=50_000.0,
        max_trades_per_day=1000,
    )


def _sma_factory(params: dict[str, int]) -> StatelessTestStrategy:
    return StatelessTestStrategy(
        short_window=params["short_window"], long_window=params["long_window"]
    )


class TestAggregateFromRealTradePnls:
    """Aggregate OOS metrics derive from REAL per-trade P&L, not synthetics."""

    def test_aggregate_total_pnl_equals_sum_of_window_pnls(self) -> None:
        """Aggregate total_pnl must reflect actual P&L (regression for #42)."""
        bars = _make_trending_bars(400)

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=100,
            test_bars=100,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result = validator.run()

        expected_pnl = sum(w.test_result.metrics.total_pnl for w in result.windows)
        assert result.aggregate_test_metrics.total_pnl == pytest.approx(expected_pnl, abs=0.01)

    def test_aggregate_trade_count_matches_concatenated_window_trades(self) -> None:
        """Aggregate trade_count equals the sum of each window's real trade count.

        With synthetic reconstruction this still held, but the aggregate
        profit_factor was forced to a degenerate two-value distribution. Here we
        also assert the aggregate matches a direct metrics computation over the
        concatenated real per-trade P&L — i.e. the true distribution is used.
        """
        from aurex_trade.metrics import calculate_metrics

        bars = _make_trending_bars(800)
        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=200,
            test_bars=200,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result = validator.run()

        real_pnls = [p for w in result.windows for p in w.test_result.trade_pnls]
        agg = result.aggregate_test_metrics
        assert agg.trade_count == len(real_pnls)
        # profit_factor is computed from the real distribution, not a synthetic one.
        direct = calculate_metrics(
            equity_curve=[100_000.0, agg.final_capital],
            trade_pnls=real_pnls,
            initial_capital=100_000.0,
        )
        assert agg.profit_factor == pytest.approx(direct.profit_factor, abs=0.01)
        assert agg.win_count == direct.win_count
        assert agg.loss_count == direct.loss_count


class TestWalkForwardValidator:
    def test_correct_number_of_windows(self) -> None:
        """Total bars / (train + test) = number of windows."""
        # 400 bars, train=100, test=100 → 2 windows
        bars = _make_trending_bars(400)

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=100,
            test_bars=100,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result = validator.run()

        assert len(result.windows) == 2

    def test_windows_non_overlapping(self) -> None:
        """Each window uses distinct bars (non-overlapping)."""
        bars = _make_trending_bars(400)

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5], "long_window": [20]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=100,
            test_bars=100,
            user_id="test",
        )
        result = validator.run()

        # Window 0 uses bars[0:200], Window 1 uses bars[200:400]
        assert result.windows[0].window_index == 0
        assert result.windows[1].window_index == 1

    def test_train_and_test_results_differ(self) -> None:
        """Train and test use different data, so results generally differ."""
        # Need enough bars per window for SMA signals to fire (long_window + extra)
        bars = _make_trending_bars(800)

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=200,
            test_bars=200,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result = validator.run()

        # At least one window should have different train vs test P&L
        any_different = any(
            w.train_result.metrics.total_pnl != w.test_result.metrics.total_pnl
            for w in result.windows
        )
        assert any_different

    def test_best_params_recorded(self) -> None:
        """Each window records which params were chosen as best."""
        bars = _make_trending_bars(400)

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=100,
            test_bars=100,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result = validator.run()

        for w in result.windows:
            assert "short_window" in w.best_params
            assert "long_window" in w.best_params
            assert w.best_params["short_window"] < w.best_params["long_window"]

    def test_aggregate_metrics_computed(self) -> None:
        """Aggregate test metrics are calculated from all windows."""
        bars = _make_trending_bars(400)

        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5, 10], "long_window": [20, 30]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=100,
            test_bars=100,
            param_validator=lambda p: p["short_window"] < p["long_window"],
            user_id="test",
        )
        result = validator.run()

        agg = result.aggregate_test_metrics
        assert agg.initial_capital == 100_000.0
        assert result.strategy_name == "stateless_test"
        assert result.symbol == "TEST"

    def test_deterministic_results(self) -> None:
        """Same inputs produce identical walk-forward results."""
        bars = _make_trending_bars(400)

        def run_wf() -> float:
            validator = WalkForwardValidator(
                strategy_factory=_sma_factory,
                param_grid={"short_window": [5, 10], "long_window": [20, 30]},
                bars=bars,
                config=_config(),
                risk_engine=_risk_engine(),
                train_bars=100,
                test_bars=100,
                param_validator=lambda p: p["short_window"] < p["long_window"],
                user_id="test",
            )
            return validator.run().aggregate_test_metrics.total_pnl

        assert run_wf() == run_wf()

    def test_configurable_window_sizes(self) -> None:
        """Different train/test sizes produce different window counts."""
        bars = _make_trending_bars(600)

        # 600 bars with train=200, test=100 → 2 windows
        validator = WalkForwardValidator(
            strategy_factory=_sma_factory,
            param_grid={"short_window": [5], "long_window": [20]},
            bars=bars,
            config=_config(),
            risk_engine=_risk_engine(),
            train_bars=200,
            test_bars=100,
            user_id="test",
        )
        result = validator.run()

        assert len(result.windows) == 2
