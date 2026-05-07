"""Strategy protocol — the contract every trading strategy must satisfy."""

from dataclasses import dataclass
from typing import Protocol

from aurex_trade.domain.models import BarData, Signal


@dataclass(frozen=True)
class ParamMeta:
    """Metadata for a single strategy parameter."""

    key: str
    label: str
    tooltip: str
    default: int | float
    min_value: int | float
    max_value: int | float


@dataclass(frozen=True)
class StrategyMetadata:
    """Self-describing metadata for a strategy."""

    display_name: str
    description: str
    params: tuple[ParamMeta, ...]


class Strategy(Protocol):
    """A trading strategy that generates signals from price data.

    Strategies are pure functions: bars in, signal out, no side effects.
    """

    @property
    def name(self) -> str: ...

    @property
    def min_bars(self) -> int: ...

    def generate(self, bars: list[BarData]) -> Signal | None: ...

    @classmethod
    def metadata(cls) -> StrategyMetadata: ...
