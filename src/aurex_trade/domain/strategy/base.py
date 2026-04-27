"""Strategy protocol — the contract every trading strategy must satisfy."""

from typing import Protocol

from aurex_trade.domain.models import BarData, Signal


class Strategy(Protocol):
    """A trading strategy that generates signals from price data.

    Strategies are pure functions: bars in, signal out, no side effects.
    """

    @property
    def name(self) -> str: ...

    def generate(self, bars: list[BarData]) -> Signal | None: ...
