"""Tests for the shared performance metrics calculator."""

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

    def test_commission_tracked(self) -> None:
        curve = [100_000.0, 99_990.0]
        result = calculate_metrics(
            curve, [-10.0], initial_capital=100_000.0, total_commission=5.0
        )
        assert result.total_commission == 5.0

    def test_result_is_frozen(self) -> None:
        result = calculate_metrics([100_000.0], [], initial_capital=100_000.0)
        assert isinstance(result, PerformanceMetrics)
