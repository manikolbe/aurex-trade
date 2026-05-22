"""Ciby Grid Hedging strategy — generates signals when price crosses grid levels."""

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata
from aurex_trade.domain.strategy.indicators import calculate_take_profit


class CibyGridHedgingStrategy:
    """Constrained grid hedging strategy for gold trading.

    Places a grid of price levels above and below an anchor price. When price
    crosses a level upward, generates a BUY signal (breakout reinforcement).
    When price crosses a level downward, generates a SELL signal. Self-limits
    to a configurable maximum number of filled levels.

    All positions receive a mandatory wide stop-loss. The strategy tracks which
    grid levels have been triggered to avoid re-entry at the same level.

    Inspired by a paired buy/sell grid approach where losing sides are stopped
    out and winning sides accumulate, gradually shifting exposure in the
    direction of the trend.
    """

    def __init__(
        self,
        grid_spacing: float = 10.0,
        max_levels: int = 6,
        stop_distance: float = 30.0,
        num_levels_above: int = 3,
        num_levels_below: int = 3,
        reward_ratio: float = 1.0,
    ) -> None:
        self._grid_spacing = grid_spacing
        self._max_levels = max_levels
        self._stop_distance = stop_distance
        self._num_levels_above = num_levels_above
        self._num_levels_below = num_levels_below
        self._reward_ratio = reward_ratio

        # Mutable internal state
        self._anchor_price: float | None = None
        self._grid_levels: list[float] = []
        self._filled_levels: dict[float, SignalType] = {}

    @property
    def name(self) -> str:
        return "ciby_grid_hedging"

    @property
    def min_bars(self) -> int:
        return 2

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="Ciby Grid Hedging",
            description=(
                "A grid trading strategy developed by legendary gold trader Ciby. "
                "Places orders at fixed price intervals around an anchor price, "
                "capturing movement in either direction without predicting which way "
                "the market will go. When price crosses a grid level upward, it "
                "generates a buy signal; when it crosses downward, a sell signal. "
                "Losing positions are stopped out while winning positions accumulate, "
                "gradually building exposure in the trending direction. Works best in "
                "ranging or slowly trending markets on instruments like gold (XAU/USD). "
                "Key risk: strong directional moves can trigger multiple stops in quick "
                "succession."
            ),
            params=(
                ParamMeta(
                    key="grid_spacing",
                    label="Grid Spacing (points)",
                    tooltip=(
                        "Distance between grid levels in price points. For gold, 10 "
                        "points is typical. Smaller spacing means more signals but "
                        "higher risk of whipsaw. Larger spacing means fewer signals "
                        "but each one captures a bigger move."
                    ),
                    default=10.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="max_levels",
                    label="Max Active Levels",
                    tooltip=(
                        "Maximum number of filled grid levels before the strategy "
                        "stops generating new signals. Acts as a self-imposed position "
                        "limit. Higher values allow more exposure but increase risk."
                    ),
                    default=6,
                    min_value=2,
                    max_value=20,
                ),
                ParamMeta(
                    key="stop_distance",
                    label="Stop Distance (points)",
                    tooltip=(
                        "Fixed stop-loss distance from entry price in points. Wide "
                        "stops (30-40 for gold) give positions room to breathe and "
                        "avoid being stopped out by normal volatility. Tighter stops "
                        "limit individual losses but may trigger too frequently."
                    ),
                    default=30.0,
                    min_value=5.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="num_levels_above",
                    label="Levels Above",
                    tooltip=(
                        "Number of grid levels to place above the anchor price. "
                        "These levels generate buy signals when crossed upward "
                        "(breakout reinforcement)."
                    ),
                    default=3,
                    min_value=1,
                    max_value=10,
                ),
                ParamMeta(
                    key="num_levels_below",
                    label="Levels Below",
                    tooltip=(
                        "Number of grid levels to place below the anchor price. "
                        "These levels generate sell signals when crossed downward "
                        "(breakout reinforcement)."
                    ),
                    default=3,
                    min_value=1,
                    max_value=10,
                ),
                ParamMeta(
                    key="reward_ratio",
                    label="Reward Ratio",
                    tooltip=(
                        "Take-profit as a multiple of stop distance. "
                        "E.g. 1.0 means TP equals the stop distance from entry. "
                        "Set to 0 to disable take-profit."
                    ),
                    default=1.0,
                    min_value=0.0,
                    max_value=5.0,
                ),
            ),
        )

    def generate(self, bars: list[BarData]) -> Signal | None:
        """Generate a signal if price crosses an unfilled grid level.

        Returns None if: insufficient data, grid is initializing, max levels
        reached, or no level crossing detected.
        """
        if len(bars) < self.min_bars:
            return None

        current_price = bars[-1].close
        prev_price = bars[-2].close

        if self._anchor_price is None:
            self._anchor_price = current_price
            self._grid_levels = self._build_grid(current_price)
            return None

        if len(self._filled_levels) >= self._max_levels:
            return None

        for level in self._grid_levels:
            if level in self._filled_levels:
                continue

            # Crossed upward: prev was below level, current is at or above
            if prev_price < level <= current_price:
                self._filled_levels[level] = SignalType.LONG
                return self._create_signal(bars[-1], level, SignalType.LONG)

            # Crossed downward: prev was above level, current is at or below
            if prev_price > level >= current_price:
                self._filled_levels[level] = SignalType.SHORT
                return self._create_signal(bars[-1], level, SignalType.SHORT)

        return None

    def get_display_state(self) -> dict[str, object] | None:
        """Return strategy-specific state for UI display.

        Returns None if the grid hasn't been initialized yet.
        """
        if self._anchor_price is None:
            return None

        levels: list[dict[str, object]] = []
        for level in reversed(self._grid_levels):
            direction = "buy" if level > self._anchor_price else "sell"
            status = "triggered" if level in self._filled_levels else "waiting"
            levels.append(
                {
                    "price": level,
                    "direction": direction,
                    "status": status,
                }
            )

        return {
            "type": "grid",
            "anchor_price": self._anchor_price,
            "levels": levels,
            "filled_count": len(self._filled_levels),
            "max_levels": self._max_levels,
        }

    def _build_grid(self, anchor: float) -> list[float]:
        """Compute sorted grid levels around the anchor price."""
        levels: list[float] = []
        for i in range(1, self._num_levels_below + 1):
            levels.append(round(anchor - i * self._grid_spacing, 2))
        for i in range(1, self._num_levels_above + 1):
            levels.append(round(anchor + i * self._grid_spacing, 2))
        return sorted(levels)

    def _create_signal(self, bar: BarData, level: float, signal_type: SignalType) -> Signal:
        """Create a Signal with fixed stop-loss distance and optional take-profit."""
        entry_price = bar.close
        if signal_type == SignalType.LONG:
            stop_loss = entry_price - self._stop_distance
        else:
            stop_loss = entry_price + self._stop_distance

        take_profit = calculate_take_profit(
            entry_price, self._stop_distance, self._reward_ratio, signal_type,
        )

        return Signal(
            symbol=bar.symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": f"{level:.2f}",
                "anchor_price": f"{self._anchor_price:.2f}",
                "filled_count": str(len(self._filled_levels)),
                "max_levels": str(self._max_levels),
                "entry_price": f"{entry_price:.5f}",
            },
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
