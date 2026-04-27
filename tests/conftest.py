"""Shared test fixtures for aurexTrade."""

from datetime import UTC, datetime

import pytest

from aurex_trade.config import AppConfig, RiskConfig, StrategyConfig
from aurex_trade.domain.enums import TradingMode
from aurex_trade.domain.models import BarData


@pytest.fixture
def utc_now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def sample_bars() -> list[BarData]:
    """Generate a list of sample price bars for testing strategies."""
    base_time = datetime(2024, 1, 1, 9, 30, tzinfo=UTC)
    prices = [
        (180.0, 181.0, 179.0, 180.5),
        (180.5, 182.0, 180.0, 181.5),
        (181.5, 183.0, 181.0, 182.0),
        (182.0, 183.5, 181.5, 183.0),
        (183.0, 184.0, 182.5, 183.5),
        (183.5, 184.5, 183.0, 184.0),
        (184.0, 185.0, 183.5, 184.5),
        (184.5, 185.5, 184.0, 185.0),
        (185.0, 186.0, 184.5, 185.5),
        (185.5, 186.5, 185.0, 186.0),
    ]
    return [
        BarData(
            timestamp=base_time.replace(minute=30 + i),
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=1000.0 + i * 100,
            symbol="GLD",
        )
        for i, (o, h, lo, c) in enumerate(prices)
    ]


@pytest.fixture
def default_config() -> AppConfig:
    """Create a default test configuration (local mode, safe defaults)."""
    return AppConfig(
        trading_mode=TradingMode.LOCAL,
        symbol="GLD",
        interval_seconds=1,
        risk=RiskConfig(
            max_position_size=10,
            max_daily_loss=500.0,
            max_trades_per_day=10,
            kill_switch=False,
        ),
        strategy=StrategyConfig(
            sma_short_window=3,
            sma_long_window=5,
        ),
    )
