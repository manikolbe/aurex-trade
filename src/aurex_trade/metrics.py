"""Shared performance metrics — used by backtesting and future live reporting.

Pure functions operating on equity curves and trade lists.
No external dependencies beyond Python stdlib.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class PerformanceMetrics:
    """Computed performance statistics for a trading period."""

    total_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    expectancy: float
    profit_factor: float
    initial_capital: float
    final_capital: float
    total_commission: float


RANKABLE_METRICS: tuple[str, ...] = tuple(
    f.name for f in PerformanceMetrics.__dataclass_fields__.values()
)


def calculate_metrics(
    equity_curve: list[float],
    trade_pnls: list[float],
    initial_capital: float,
    total_commission: float = 0.0,
    risk_free_rate: float = 0.0,
    equity_timestamps: list[datetime] | None = None,
) -> PerformanceMetrics:
    """Calculate performance metrics from an equity curve and trade P&L list.

    Args:
        equity_curve: List of equity values at each time step.
        trade_pnls: List of P&L for each individual trade (after commission).
        initial_capital: Starting capital.
        total_commission: Total commission paid across all trades.
        risk_free_rate: Annualized risk-free rate for Sharpe calculation.
        equity_timestamps: Optional timestamps aligned to ``equity_curve``. When
            provided, Sharpe is computed on daily-resampled returns (the only
            statistically meaningful basis for sparse intraday data). When omitted,
            Sharpe falls back to per-step returns annualized over the curve length.

    Returns:
        Frozen PerformanceMetrics dataclass.
    """
    final_capital = equity_curve[-1] if equity_curve else initial_capital
    total_pnl = final_capital - initial_capital

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    trade_count = len(trade_pnls)
    win_rate = win_count / trade_count if trade_count > 0 else 0.0

    expectancy = total_pnl / trade_count if trade_count > 0 else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    max_dd, max_dd_pct = _max_drawdown(equity_curve)
    sharpe = _sharpe_ratio(equity_curve, risk_free_rate, equity_timestamps)

    return PerformanceMetrics(
        total_pnl=round(total_pnl, 2),
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=round(win_rate, 4),
        max_drawdown=round(max_dd, 2),
        max_drawdown_pct=round(max_dd_pct, 4),
        sharpe_ratio=round(sharpe, 4),
        expectancy=round(expectancy, 2),
        profit_factor=round(profit_factor, 4),
        initial_capital=initial_capital,
        final_capital=round(final_capital, 2),
        total_commission=round(total_commission, 2),
    )


def _max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """Calculate maximum drawdown (absolute and percentage).

    Returns:
        Tuple of (max_drawdown_absolute, max_drawdown_percentage).
    """
    if len(equity_curve) < 2:
        return 0.0, 0.0

    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0

    for equity in equity_curve[1:]:
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        dd_pct = drawdown / peak if peak > 0 else 0.0
        if drawdown > max_dd:
            max_dd = drawdown
            max_dd_pct = dd_pct

    return max_dd, max_dd_pct


def _sharpe_ratio(
    equity_curve: list[float],
    risk_free_rate: float = 0.0,
    equity_timestamps: list[datetime] | None = None,
) -> float:
    """Annualized Sharpe ratio from an equity curve.

    Preferred basis: daily-resampled returns. Per-bar M1 returns are dominated by
    idle (zero-return) bars, which deflates the ratio and makes it near-meaningless;
    resampling to one return per calendar day fixes that. Daily returns annualize by
    sqrt(252).

    Falls back to per-step returns (annualized over the curve length) when no
    timestamps are supplied — e.g. synthetic test curves or chained aggregate curves
    that carry no time axis.
    """
    if len(equity_curve) < 2:
        return 0.0

    if equity_timestamps is not None and len(equity_timestamps) == len(equity_curve):
        daily = _resample_daily(equity_curve, equity_timestamps)
        returns = _step_returns(daily)
        periods_per_year = TRADING_DAYS_PER_YEAR
        rf_per_period = risk_free_rate / periods_per_year
    else:
        returns = _step_returns(equity_curve)
        # No time axis: keep the legacy per-minute-bar annualization.
        periods_per_year = TRADING_DAYS_PER_YEAR * 1440
        rf_per_period = risk_free_rate / periods_per_year

    return _annualized_sharpe(returns, rf_per_period, periods_per_year)


def _step_returns(values: list[float]) -> list[float]:
    """Simple period-over-period returns, skipping zero denominators."""
    return [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]


def _resample_daily(equity_curve: list[float], timestamps: list[datetime]) -> list[float]:
    """Reduce an equity curve to one (closing) value per UTC calendar day."""
    by_day: dict[date, float] = {}
    for equity, ts in zip(equity_curve, timestamps, strict=True):
        by_day[ts.date()] = equity  # later value on a day overwrites — daily close
    return [by_day[day] for day in sorted(by_day)]


def _annualized_sharpe(
    returns: list[float], rf_per_period: float, periods_per_year: int
) -> float:
    """Annualized Sharpe from a list of equal-spaced periodic returns."""
    if not returns:
        return 0.0

    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    std_return = math.sqrt(variance)

    if std_return == 0:
        return 0.0

    sharpe_per_period = (mean_return - rf_per_period) / std_return
    return sharpe_per_period * math.sqrt(periods_per_year)
