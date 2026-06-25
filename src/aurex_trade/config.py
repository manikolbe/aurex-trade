"""Application configuration — type-safe, validated, loaded from .env."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from aurex_trade.domain.enums import TradingMode


class OANDAConfig(BaseSettings):
    """OANDA connection settings."""

    model_config = SettingsConfigDict(env_prefix="OANDA_", env_file=".env", extra="ignore")

    access_token: str = ""  # OANDA API access token (from env var OANDA_ACCESS_TOKEN)
    account_id: str = ""  # OANDA account ID (from env var OANDA_ACCOUNT_ID)
    server: str = "practice"  # "practice" or "live"


class RiskConfig(BaseSettings):
    """Risk management parameters."""

    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    max_position_size: int = 10
    max_daily_loss: float = 500.0
    max_trades_per_day: int = 10
    kill_switch: bool = False
    require_stop_loss: bool = True
    risk_per_trade: float = 0.02
    max_drawdown_pct: float = 0.20
    max_consecutive_losses: int = 5


class StrategyConfig(BaseSettings):
    """Strategy parameters (Ciby Sliding Grid — the CLI local-mode default).

    Defaults mirror ``CibySlidingGridStrategy.metadata()``.
    """

    model_config = SettingsConfigDict(env_prefix="STRATEGY_", env_file=".env", extra="ignore")

    grid_spacing: float = 10.0
    anchor_gap: float = 15.0
    buy_sell_offset: float = 0.90
    anchor_units: float = 10.0
    grid_units: float = 20.0
    stop_buffer: float = 1.0
    max_levels_ahead: int = 2
    max_levels_behind: int = 1
    session_profit_target: float = 100.0
    session_loss_limit: float = 50.0
    daily_loss_limit: float = 200.0


class AppConfig(BaseSettings):
    """Root application configuration.

    Loaded from environment variables and .env file.
    Nested configs use prefixed env vars (e.g., OANDA_ACCESS_TOKEN, RISK_MAX_DAILY_LOSS).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    trading_mode: TradingMode = TradingMode.LOCAL
    symbol: str = "XAU_USD"
    interval_seconds: int = 60
    db_path: Path = Path("data/aurex_trade.db")
    log_level: str = "INFO"

    # Live trading double-gate safety
    live_trading_confirmed: bool = False

    # Nested configs
    oanda: OANDAConfig = Field(default_factory=OANDAConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
