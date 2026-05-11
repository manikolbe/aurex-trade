"""Backtest configuration — validated settings for a backtest run."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BacktestConfig(BaseSettings):
    """Configuration for a single backtest run.

    Can be loaded from environment variables (prefix BACKTEST_) or
    constructed directly in code.
    """

    model_config = SettingsConfigDict(
        env_prefix="BACKTEST_", env_file=".env", extra="ignore"
    )

    symbol: str = "XAU_USD"
    granularity: str = "M1"
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 100_000.0
    position_size: float = 1.0
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    commission_per_trade: float = 0.0
    deterministic_seed: int = 42
    bar_count: int = 50
