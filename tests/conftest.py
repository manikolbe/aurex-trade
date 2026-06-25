"""Shared test fixtures for AurexTrade."""

from datetime import UTC, datetime

import pytest

from aurex_trade.config import AppConfig, RiskConfig, StrategyConfig
from aurex_trade.domain.enums import SignalType, TradingMode
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata


class StatelessTestStrategy:
    """A minimal stateless strategy for exercising the engine's simple-mode path.

    Replaces the former SMACrossover in engine/integration tests after the stale
    strategies were removed. It is intentionally simple and deterministic: it
    crosses a short and long simple moving average and emits LONG/SHORT signals on
    the cross, mirroring the *shape* of signal flow (intermittent signals with a
    stop-loss) the engine's risk-gated simple mode needs — without depending on any
    production strategy. Satisfies the Strategy Protocol.
    """

    def __init__(self, short_window: int = 3, long_window: int = 5) -> None:
        self._short = short_window
        self._long = long_window

    @property
    def name(self) -> str:
        return "stateless_test"

    @property
    def min_bars(self) -> int:
        return self._long + 1

    def generate(self, bars: list[BarData]) -> Signal | None:
        if len(bars) < self._long + 1:
            return None
        closes = [b.close for b in bars]
        short_now = sum(closes[-self._short :]) / self._short
        long_now = sum(closes[-self._long :]) / self._long
        short_prev = sum(closes[-self._short - 1 : -1]) / self._short
        long_prev = sum(closes[-self._long - 1 : -1]) / self._long
        entry = closes[-1]
        if short_prev <= long_prev and short_now > long_now:
            return Signal(
                symbol=bars[-1].symbol,
                signal_type=SignalType.LONG,
                strategy_name=self.name,
                strength=1.0,
                stop_loss=entry - 1.0,
            )
        if short_prev >= long_prev and short_now < long_now:
            return Signal(
                symbol=bars[-1].symbol,
                signal_type=SignalType.SHORT,
                strategy_name=self.name,
                strength=1.0,
                stop_loss=entry + 1.0,
            )
        return None

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="Stateless Test Strategy",
            description="Test-only stateless SMA-cross strategy.",
            params=(
                ParamMeta("short_window", "Short Window", "Short SMA period", 3, 1, 50),
                ParamMeta("long_window", "Long Window", "Long SMA period", 5, 2, 200),
            ),
        )


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
        strategy=StrategyConfig(),
    )
