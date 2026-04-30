"""Settings endpoint — view current configuration (secrets redacted)."""

from __future__ import annotations

from fastapi import APIRouter

from aurex_trade.config import AppConfig
from aurex_trade.web.schemas import SettingsResponse

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings() -> SettingsResponse:
    """Return current application settings (secrets redacted)."""
    config = AppConfig()
    return SettingsResponse(
        trading_mode=config.trading_mode.value,
        symbol=config.symbol,
        interval_seconds=config.interval_seconds,
        log_level=config.log_level,
    )
