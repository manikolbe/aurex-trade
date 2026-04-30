"""Backtest result models — trade records and aggregate results.

These are backtest-specific. Shared metrics live in aurex_trade.metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aurex_trade.domain.models import Signal, Trade
from aurex_trade.metrics import PerformanceMetrics


@dataclass(frozen=True)
class BacktestTradeRecord:
    """A single trade with its context during the backtest."""

    trade: Trade
    signal: Signal
    bar_index: int
    equity_after: float


@dataclass(frozen=True)
class BacktestResult:
    """Complete result of a backtest run."""

    metrics: PerformanceMetrics
    equity_curve: list[float] = field(default_factory=list)
    trades: list[BacktestTradeRecord] = field(default_factory=list)
    strategy_name: str = ""
    symbol: str = ""
    start_date: datetime | None = None
    end_date: datetime | None = None
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SweepResult:
    """Results of a parameter sweep — all runs ranked by metric."""

    results: list[BacktestResult]
    rank_metric: str
    symbol: str
    total_combinations: int


@dataclass(frozen=True)
class WalkForwardWindow:
    """Results from one train/test window."""

    train_result: BacktestResult
    test_result: BacktestResult
    best_params: dict[str, int]
    window_index: int


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregate walk-forward validation results."""

    windows: list[WalkForwardWindow]
    aggregate_test_metrics: PerformanceMetrics
    strategy_name: str
    symbol: str
