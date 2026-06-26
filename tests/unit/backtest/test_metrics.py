"""Tests for the shared performance metrics calculator."""

import math
from datetime import UTC, datetime, timedelta

from aurex_trade.metrics import PerformanceMetrics, calculate_metrics


class TestCalculateMetrics:
    def test_empty_equity_curve(self) -> None:
        result = calculate_metrics([], [], initial_capital=100_000.0)
        assert result.total_pnl == 0.0
        assert result.trade_count == 0
        assert result.final_capital == 100_000.0

    def test_no_trades(self) -> None:
        # Equity stays flat
        curve = [100_000.0] * 10
        result = calculate_metrics(curve, [], initial_capital=100_000.0)
        assert result.total_pnl == 0.0
        assert result.win_rate == 0.0
        assert result.max_drawdown == 0.0

    def test_winning_trades(self) -> None:
        curve = [100_000.0, 100_100.0, 100_300.0, 100_600.0]
        pnls = [100.0, 200.0, 300.0]
        result = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert result.total_pnl == 600.0
        assert result.trade_count == 3
        assert result.win_count == 3
        assert result.loss_count == 0
        assert result.win_rate == 1.0
        assert result.final_capital == 100_600.0
        assert result.profit_factor == float("inf")

    def test_losing_trades(self) -> None:
        curve = [100_000.0, 99_900.0, 99_700.0]
        pnls = [-100.0, -200.0]
        result = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert result.total_pnl == -300.0
        assert result.win_count == 0
        assert result.loss_count == 2
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0

    def test_mixed_trades(self) -> None:
        curve = [100_000.0, 100_200.0, 99_900.0, 100_100.0]
        pnls = [200.0, -300.0, 200.0]
        result = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert result.total_pnl == 100.0
        assert result.win_count == 2
        assert result.loss_count == 1
        assert result.win_rate == 0.6667  # 2/3 rounded to 4 decimal places
        assert result.expectancy == 33.33  # 100/3

    def test_profit_factor(self) -> None:
        curve = [100_000.0, 100_500.0, 100_200.0]
        pnls = [500.0, -300.0]
        result = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert result.profit_factor == round(500.0 / 300.0, 4)

    def test_max_drawdown(self) -> None:
        # Peak at 100_500, drops to 99_800 = drawdown of 700
        curve = [100_000.0, 100_500.0, 100_200.0, 99_800.0, 100_100.0]
        pnls = [500.0, -300.0, -400.0, 300.0]
        result = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert result.max_drawdown == 700.0
        expected_pct = 700.0 / 100_500.0
        assert abs(result.max_drawdown_pct - expected_pct) < 0.0001

    def test_sharpe_ratio_flat_equity_is_zero(self) -> None:
        curve = [100_000.0] * 100
        result = calculate_metrics(curve, [], initial_capital=100_000.0)
        assert result.sharpe_ratio == 0.0

    def test_sharpe_ratio_positive_for_steady_gains(self) -> None:
        # Steady linear growth should have high Sharpe
        curve = [100_000.0 + i * 10.0 for i in range(100)]
        pnls = [10.0] * 99
        result = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert result.sharpe_ratio > 0

    def test_sharpe_uses_daily_returns_when_timestamps_given(self) -> None:
        """With timestamps, Sharpe is computed on daily closes, annualized by sqrt(252).

        Three trading days with flat idle bars in between. Resampling keeps the
        daily close of each day, giving two daily returns — and the result matches
        a hand-computed daily Sharpe, proving the per-bar idle drag is gone.
        """
        days = [datetime(2025, 1, d, tzinfo=UTC) for d in (2, 3, 6)]
        closes = [100_400.0, 100_900.0, 101_200.0]
        curve = [100_000.0]
        timestamps = [days[0]]
        for day, close in zip(days, closes, strict=True):
            for i in range(5):  # 5 intraday points per day; close carried flat
                curve.append(close)
                timestamps.append(day + timedelta(minutes=i))

        with_ts = calculate_metrics(
            curve, [400.0, 500.0, 300.0], initial_capital=100_000.0,
            equity_timestamps=timestamps,
        )
        without_ts = calculate_metrics(curve, [400.0, 500.0, 300.0], initial_capital=100_000.0)

        # Hand-computed: daily closes 100_400 -> 100_900 -> 101_200.
        r1 = (100_900.0 - 100_400.0) / 100_400.0
        r2 = (101_200.0 - 100_900.0) / 100_900.0
        mean = (r1 + r2) / 2
        std = math.sqrt(((r1 - mean) ** 2 + (r2 - mean) ** 2) / 2)
        expected = (mean / std) * math.sqrt(252)
        assert with_ts.sharpe_ratio == round(expected, 4)
        # And it genuinely differs from the (meaningless) per-bar fallback.
        assert with_ts.sharpe_ratio != without_ts.sharpe_ratio

    def test_mismatched_timestamps_fall_back_to_per_step(self) -> None:
        """A timestamp list of the wrong length is ignored (legacy path used)."""
        curve = [100_000.0 + i * 10.0 for i in range(100)]
        pnls = [10.0] * 99
        bad_ts = [datetime(2025, 1, 2, tzinfo=UTC)]  # len 1, curve len 100
        with_bad = calculate_metrics(
            curve, pnls, initial_capital=100_000.0, equity_timestamps=bad_ts
        )
        legacy = calculate_metrics(curve, pnls, initial_capital=100_000.0)
        assert with_bad.sharpe_ratio == legacy.sharpe_ratio

    def test_commission_tracked(self) -> None:
        curve = [100_000.0, 99_990.0]
        result = calculate_metrics(curve, [-10.0], initial_capital=100_000.0, total_commission=5.0)
        assert result.total_commission == 5.0

    def test_result_is_frozen(self) -> None:
        result = calculate_metrics([100_000.0], [], initial_capital=100_000.0)
        assert isinstance(result, PerformanceMetrics)
