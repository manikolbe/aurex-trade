"""Pydantic request/response models for the web API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from aurex_trade.backtest.results import BacktestResult, SweepResult, WalkForwardResult
from aurex_trade.metrics import PerformanceMetrics
from aurex_trade.web.tasks import TaskInfo, TaskStatus

# Constrained types for safe string inputs
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GRANULARITY_VALUES = {"S5", "S10", "S15", "S30", "M1", "M2", "M4", "M5", "M10",
                       "M15", "M30", "H1", "H2", "H3", "H4", "H6", "H8", "H12", "D", "W", "M"}

Symbol = Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,20}$")]
Granularity = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,3}$")]

# --- Health ---


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    timestamp: datetime
    version: str


# --- Tasks ---


class TaskStatusResponse(BaseModel):
    """Background task status response."""

    id: UUID
    task_type: str
    status: TaskStatus
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class TaskSubmittedResponse(BaseModel):
    """Response when a background task is submitted."""

    task_id: UUID
    task_type: str
    status: TaskStatus


# --- Metrics ---


class MetricsResponse(BaseModel):
    """Performance metrics response."""

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


# --- Backtest ---


class BacktestRequest(BaseModel):
    """Request to run a single backtest."""

    symbol: Symbol = "XAU_USD"
    granularity: Granularity = "M1"
    start_date: str = ""
    end_date: str = ""
    capital: float = Field(default=100_000.0, gt=0)
    position_size: float = Field(default=1.0, gt=0)
    short_window: int = Field(default=10, gt=0)
    long_window: int = Field(default=30, gt=0)
    spread: float = Field(default=0.6, ge=0)
    slippage: float = Field(default=0.2, ge=0)
    commission: float = Field(default=0.0, ge=0)
    seed: int = 42
    max_position: int = Field(default=10, gt=0)
    max_daily_loss: float = Field(default=500.0, gt=0)
    max_trades_per_day: int = Field(default=100, gt=0)
    risk_per_trade: float = Field(default=0.02, gt=0, le=1.0)
    max_drawdown_pct: float = Field(default=0.20, gt=0, le=1.0)
    max_consecutive_losses: int = Field(default=5, gt=0)
    require_stop_loss: bool = True

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date format if provided."""
        if v and not _DATE_PATTERN.match(v):
            msg = "Date must be in YYYY-MM-DD format"
            raise ValueError(msg)
        return v

    @field_validator("granularity")
    @classmethod
    def validate_granularity(cls, v: str) -> str:
        """Validate granularity is a known OANDA value."""
        if v not in _GRANULARITY_VALUES:
            msg = f"Unknown granularity: {v}"
            raise ValueError(msg)
        return v


class BacktestResultResponse(BaseModel):
    """Backtest result response."""

    metrics: MetricsResponse
    strategy_name: str
    symbol: str
    start_date: datetime | None = None
    end_date: datetime | None = None
    parameters: dict[str, str]
    trade_count: int
    equity_curve_length: int
    equity_curve: list[float] = []


# --- Sweep ---


class SweepRequest(BaseModel):
    """Request to run a parameter sweep."""

    strategy: str = "sma_crossover"
    params: dict[str, list[int]] = Field(..., max_length=10)
    symbol: Symbol = "XAU_USD"
    granularity: Granularity = "M1"
    start_date: str = ""
    end_date: str = ""
    capital: float = Field(default=100_000.0, gt=0)
    position_size: float = Field(default=1.0, gt=0)
    spread: float = Field(default=0.6, ge=0)
    slippage: float = Field(default=0.2, ge=0)
    commission: float = Field(default=0.0, ge=0)
    seed: int = 42
    max_position: int = Field(default=10, gt=0)
    max_daily_loss: float = Field(default=5000.0, gt=0)
    max_trades_per_day: int = Field(default=100, gt=0)
    risk_per_trade: float = Field(default=0.02, gt=0, le=1.0)
    max_drawdown_pct: float = Field(default=0.20, gt=0, le=1.0)
    max_consecutive_losses: int = Field(default=5, gt=0)
    require_stop_loss: bool = True
    rank_by: str = "sharpe_ratio"

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date format if provided."""
        if v and not _DATE_PATTERN.match(v):
            msg = "Date must be in YYYY-MM-DD format"
            raise ValueError(msg)
        return v

    @field_validator("granularity")
    @classmethod
    def validate_granularity(cls, v: str) -> str:
        """Validate granularity is a known OANDA value."""
        if v not in _GRANULARITY_VALUES:
            msg = f"Unknown granularity: {v}"
            raise ValueError(msg)
        return v

    @field_validator("params")
    @classmethod
    def validate_params(cls, v: dict[str, list[int]]) -> dict[str, list[int]]:
        """Limit parameter grid size to prevent combinatorial explosion."""
        total_combos = 1
        for values in v.values():
            if len(values) > 50:
                msg = "Each parameter list must have at most 50 values"
                raise ValueError(msg)
            total_combos *= len(values)
        if total_combos > 1000:
            msg = f"Total parameter combinations ({total_combos}) exceeds limit of 1000"
            raise ValueError(msg)
        return v


class SweepResultResponse(BaseModel):
    """Sweep result response."""

    results: list[BacktestResultResponse]
    rank_metric: str
    symbol: str
    total_combinations: int


# --- Walk-Forward ---


class WalkForwardRequest(BaseModel):
    """Request to run walk-forward validation."""

    strategy: str = "sma_crossover"
    params: dict[str, list[int]] = Field(..., max_length=10)
    symbol: Symbol = "XAU_USD"
    granularity: Granularity = "M1"
    start_date: str = ""
    end_date: str = ""
    capital: float = Field(default=100_000.0, gt=0)
    position_size: float = Field(default=1.0, gt=0)
    spread: float = Field(default=0.6, ge=0)
    slippage: float = Field(default=0.2, ge=0)
    commission: float = Field(default=0.0, ge=0)
    seed: int = 42
    max_position: int = Field(default=10, gt=0)
    max_daily_loss: float = Field(default=5000.0, gt=0)
    max_trades_per_day: int = Field(default=100, gt=0)
    risk_per_trade: float = Field(default=0.02, gt=0, le=1.0)
    max_drawdown_pct: float = Field(default=0.20, gt=0, le=1.0)
    max_consecutive_losses: int = Field(default=5, gt=0)
    require_stop_loss: bool = True
    rank_by: str = "sharpe_ratio"

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: str) -> str:
        """Validate date format if provided."""
        if v and not _DATE_PATTERN.match(v):
            msg = "Date must be in YYYY-MM-DD format"
            raise ValueError(msg)
        return v

    @field_validator("granularity")
    @classmethod
    def validate_granularity(cls, v: str) -> str:
        """Validate granularity is a known OANDA value."""
        if v not in _GRANULARITY_VALUES:
            msg = f"Unknown granularity: {v}"
            raise ValueError(msg)
        return v

    @field_validator("params")
    @classmethod
    def validate_params(cls, v: dict[str, list[int]]) -> dict[str, list[int]]:
        """Limit parameter grid size to prevent combinatorial explosion."""
        total_combos = 1
        for values in v.values():
            if len(values) > 50:
                msg = "Each parameter list must have at most 50 values"
                raise ValueError(msg)
            total_combos *= len(values)
        if total_combos > 1000:
            msg = f"Total parameter combinations ({total_combos}) exceeds limit of 1000"
            raise ValueError(msg)
        return v
    train_bars: int = Field(default=7200, gt=0)
    test_bars: int = Field(default=7200, gt=0)


class WalkForwardWindowResponse(BaseModel):
    """Single walk-forward window response."""

    window_index: int
    best_params: dict[str, int]
    train_metrics: MetricsResponse
    test_metrics: MetricsResponse


class WalkForwardResultResponse(BaseModel):
    """Walk-forward validation result response."""

    windows: list[WalkForwardWindowResponse]
    aggregate_test_metrics: MetricsResponse
    strategy_name: str
    symbol: str


# --- Bot ---


class BotStatusResponse(BaseModel):
    """Bot status response."""

    running: bool
    task_id: UUID | None = None


# --- Settings ---


class SettingsResponse(BaseModel):
    """Application settings (secrets redacted)."""

    trading_mode: str
    symbol: str
    interval_seconds: int
    log_level: str


# --- Converters ---


def metrics_to_response(m: PerformanceMetrics) -> MetricsResponse:
    """Convert domain PerformanceMetrics to API response."""
    return MetricsResponse(
        total_pnl=m.total_pnl,
        trade_count=m.trade_count,
        win_count=m.win_count,
        loss_count=m.loss_count,
        win_rate=m.win_rate,
        max_drawdown=m.max_drawdown,
        max_drawdown_pct=m.max_drawdown_pct,
        sharpe_ratio=m.sharpe_ratio,
        expectancy=m.expectancy,
        profit_factor=m.profit_factor,
        initial_capital=m.initial_capital,
        final_capital=m.final_capital,
        total_commission=m.total_commission,
    )


def _downsample_curve(curve: list[float], max_points: int = 500) -> list[float]:
    """Downsample an equity curve to at most max_points, keeping first and last."""
    n = len(curve)
    if n <= max_points:
        return curve
    step = (n - 1) / (max_points - 1)
    indices = [int(i * step) for i in range(max_points - 1)]
    indices.append(n - 1)
    return [curve[i] for i in indices]


def backtest_result_to_response(r: BacktestResult) -> BacktestResultResponse:
    """Convert domain BacktestResult to API response."""
    return BacktestResultResponse(
        metrics=metrics_to_response(r.metrics),
        strategy_name=r.strategy_name,
        symbol=r.symbol,
        start_date=r.start_date,
        end_date=r.end_date,
        parameters=r.parameters,
        trade_count=len(r.trades),
        equity_curve_length=len(r.equity_curve),
        equity_curve=_downsample_curve(r.equity_curve),
    )


def sweep_result_to_response(r: SweepResult) -> SweepResultResponse:
    """Convert domain SweepResult to API response."""
    return SweepResultResponse(
        results=[backtest_result_to_response(br) for br in r.results],
        rank_metric=r.rank_metric,
        symbol=r.symbol,
        total_combinations=r.total_combinations,
    )


def walk_forward_result_to_response(r: WalkForwardResult) -> WalkForwardResultResponse:
    """Convert domain WalkForwardResult to API response."""
    return WalkForwardResultResponse(
        windows=[
            WalkForwardWindowResponse(
                window_index=w.window_index,
                best_params=w.best_params,
                train_metrics=metrics_to_response(w.train_result.metrics),
                test_metrics=metrics_to_response(w.test_result.metrics),
            )
            for w in r.windows
        ],
        aggregate_test_metrics=metrics_to_response(r.aggregate_test_metrics),
        strategy_name=r.strategy_name,
        symbol=r.symbol,
    )


def task_info_to_response(info: TaskInfo) -> TaskStatusResponse:
    """Convert TaskInfo to API response."""
    return TaskStatusResponse(
        id=info.id,
        task_type=info.task_type,
        status=info.status,
        created_at=info.created_at,
        completed_at=info.completed_at,
        error=info.error,
    )
