"""Application configuration — type-safe, validated, loaded from .env."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aurex_trade.domain.enums import TradingMode


class IBKRConfig(BaseSettings):
    """IBKR connection settings (NOT credentials — just host/port config)."""

    model_config = SettingsConfigDict(env_prefix="IBKR_")

    host: str = "127.0.0.1"
    port: int = 7497        # 7497 = paper, 7496 = live
    client_id: int = 1


class RiskConfig(BaseSettings):
    """Risk management parameters."""

    model_config = SettingsConfigDict(env_prefix="RISK_")

    max_position_size: int = 10
    max_daily_loss: float = 500.0
    max_trades_per_day: int = 10
    kill_switch: bool = False


class StrategyConfig(BaseSettings):
    """Strategy parameters (SMA Crossover)."""

    model_config = SettingsConfigDict(env_prefix="STRATEGY_")

    sma_short_window: int = 10
    sma_long_window: int = 30


class AppConfig(BaseSettings):
    """Root application configuration.

    Loaded from environment variables and .env file.
    Nested configs use prefixed env vars (e.g., IBKR_HOST, RISK_MAX_DAILY_LOSS).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    trading_mode: TradingMode = TradingMode.LOCAL
    symbol: str = "GLD"
    interval_seconds: int = 60
    db_path: Path = Path("data/aurex_trade.db")
    log_level: str = "INFO"

    # Live trading double-gate safety
    live_trading_confirmed: bool = False

    # Nested configs
    ibkr: IBKRConfig = Field(default_factory=IBKRConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
