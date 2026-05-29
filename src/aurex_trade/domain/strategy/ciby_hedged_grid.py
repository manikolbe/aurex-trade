"""Ciby Hedged Grid strategy — pre-placed limit orders at rounded grid levels."""

import math
from collections import deque
from uuid import uuid4

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata


class CibyHedgedGridStrategy:
    """Directional-agnostic grid strategy using pre-placed limit orders.

    Places buy + sell limit orders at grid levels (rounded to nearest multiple
    of grid_spacing) ahead of current price. When a level fills, the next level
    in that direction is placed. Stop distance equals grid_spacing.

    Risk is managed via:
    - Session profit target: cancel pending + close all & restart fresh
    - Session loss limit: cancel pending + close all & restart fresh
    - Daily loss limit: cancel pending + close all & stop trading for the day
    """

    _LEVELS_AHEAD = 2  # How many levels to maintain in each direction

    def __init__(
        self,
        grid_spacing: float = 10.0,
        grid_units: float = 10.0,
        session_profit_target: float = 100.0,
        session_loss_limit: float = 50.0,
        daily_loss_limit: float = 200.0,
    ) -> None:
        self._grid_spacing = grid_spacing
        self._grid_units = grid_units
        self._stop_distance = grid_spacing  # SL = grid_spacing
        self._session_profit_target = session_profit_target
        self._session_loss_limit = session_loss_limit
        self._daily_loss_limit = daily_loss_limit

        # Mutable session state
        self._symbol: str = ""
        self._anchor_price: float | None = None
        self._grid_levels: list[float] = []
        self._signal_queue: deque[Signal] = deque()
        self._placed_levels: set[float] = set()  # Levels with pending orders at broker
        self._filled_levels: dict[float, str] = {}  # level → pair_id (active trades)
        self._filled_entry_prices: dict[float, dict[str, float]] = {}
        self._pair_closed_sides: dict[str, set[str]] = {}
        self._session_realized_pnl: float = 0.0
        self._session_unrealized_pnl: float = 0.0
        self._daily_realized_pnl: float = 0.0
        self._session_active: bool = True
        self._current_date: str = ""
        self._close_all_pending: bool = False
        self._close_reason: str = ""
        self._session_count: int = 1
        self._session_history: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "ciby_hedged_grid"

    @property
    def min_bars(self) -> int:
        return 1

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="Ciby Hedged Grid",
            description=(
                "A grid trading strategy developed by legendary gold trader Ciby. "
                "Pre-places hedged limit orders (buy + sell) at rounded grid levels "
                "above and below the current price. Orders fill at exact prices with "
                "zero slippage. Stop distance equals grid spacing. When a level fills, "
                "the next level in that direction is automatically placed. Risk is "
                "managed via session profit targets, session loss limits, and daily "
                "loss limits. Works best on volatile instruments like gold (XAU/USD)."
            ),
            params=(
                ParamMeta(
                    key="grid_spacing",
                    label="Grid Spacing ($)",
                    tooltip=(
                        "Distance between grid levels in dollars. Levels are placed at "
                        "round multiples of this value (e.g. spacing=10, levels at 4550, "
                        "4560, 4570...). Stop distance also equals this value."
                    ),
                    default=10.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="grid_units",
                    label="Grid Units",
                    tooltip=(
                        "Position size (units) for each limit order. Both buy and sell "
                        "at each level use this size."
                    ),
                    default=10.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="session_profit_target",
                    label="Session Profit Target ($)",
                    tooltip=(
                        "When total session P&L reaches this target, cancel all pending "
                        "orders, close all positions, and restart fresh at current price."
                    ),
                    default=100.0,
                    min_value=10.0,
                    max_value=10000.0,
                ),
                ParamMeta(
                    key="session_loss_limit",
                    label="Session Loss Limit ($)",
                    tooltip=(
                        "When total session P&L drops below this negative threshold, "
                        "cancel all pending, close all positions, and restart fresh."
                    ),
                    default=50.0,
                    min_value=10.0,
                    max_value=10000.0,
                ),
                ParamMeta(
                    key="daily_loss_limit",
                    label="Daily Loss Limit ($)",
                    tooltip=(
                        "When cumulative P&L across all sessions for the day drops "
                        "below this negative threshold, stop trading entirely. Resumes "
                        "automatically the next day."
                    ),
                    default=200.0,
                    min_value=10.0,
                    max_value=50000.0,
                ),
            ),
        )

    def update_unrealized_pnl(self, unrealized_pnl: float) -> None:
        """Called by engine each cycle with current unrealized P&L from broker."""
        self._session_unrealized_pnl = unrealized_pnl

    def generate(self, bars: list[BarData]) -> Signal | None:
        """Generate signals: drain queue, check P&L exits, or place initial levels."""
        if not bars:
            return None

        current_bar = bars[-1]
        current_date = current_bar.timestamp.strftime("%Y-%m-%d")

        # Day boundary reset
        if self._current_date and current_date != self._current_date:
            self._daily_realized_pnl = 0.0
            self._session_active = True
            self._session_count = 1
            self._session_history = []
            self._restart_session()
        self._current_date = current_date

        # Drain signal queue (all queued signals processed in one cycle)
        if self._signal_queue:
            return self._signal_queue.popleft()

        if self._close_all_pending:
            self._close_all_pending = False
            return self._flat_close_all(current_bar, self._close_reason)

        if not self._session_active:
            return None

        # Check session P&L limits (realized + unrealized)
        if self._anchor_price is not None:
            total_session_pnl = self._session_realized_pnl + self._session_unrealized_pnl
            if total_session_pnl >= self._session_profit_target:
                self._trigger_close_all("session_profit_target")
                return self._flat_close_all(current_bar, "session_profit_target")
            if total_session_pnl <= -self._session_loss_limit:
                self._trigger_close_all("session_loss_limit")
                return self._flat_close_all(current_bar, "session_loss_limit")

        # Initialize session — calculate grid and place initial limit orders
        if self._anchor_price is None:
            self._anchor_price = current_bar.close
            self._grid_levels = self._build_grid(current_bar.close)
            self._place_initial_levels(current_bar)
            if self._signal_queue:
                return self._signal_queue.popleft()

        return None

    def report_fill(self, grid_level_key: str, fill_price: float) -> None:
        """Called by engine when a limit order fills at the broker.

        Marks the side as active and triggers replenishment of the next level.
        """
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return
        level_str, side = parts
        try:
            level = float(level_str)
        except ValueError:
            return

        # Record fill price for display
        if level in self._filled_entry_prices:
            self._filled_entry_prices[level][side] = fill_price

        # If this is the first fill at this level, mark it as active
        if level in self._placed_levels and level not in self._filled_levels:
            pair_id = str(uuid4())
            self._filled_levels[level] = pair_id
            self._placed_levels.discard(level)

        # Replenish: place the next level beyond this one
        self._replenish_level(level, side)

    def report_trade_closed(self, grid_level_key: str, realized_pnl: float) -> None:
        """Called by engine when a broker-side closure is detected.

        Updates session/daily P&L and releases the grid level when both sides close.
        """
        self._session_realized_pnl += realized_pnl
        self._daily_realized_pnl += realized_pnl

        if self._daily_realized_pnl <= -self._daily_loss_limit:
            self._close_reason = "daily_loss_limit"
            self._close_all_pending = True
            self._session_active = False

        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return
        level_str, side = parts
        try:
            level = float(level_str)
        except ValueError:
            return

        pair_id = self._filled_levels.get(level)
        if pair_id is None:
            return

        if pair_id not in self._pair_closed_sides:
            self._pair_closed_sides[pair_id] = set()
        self._pair_closed_sides[pair_id].add(side)

        if len(self._pair_closed_sides[pair_id]) >= 2:
            del self._filled_levels[level]
            del self._pair_closed_sides[pair_id]
            self._filled_entry_prices.pop(level, None)

    def on_signal_rejected(self, grid_level_key: str) -> None:
        """Called by engine when risk rejects a signal from this strategy."""
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return
        level_str, _side = parts
        try:
            level = float(level_str)
        except ValueError:
            return

        # Remove queued partner signals for this level
        self._signal_queue = deque(
            s for s in self._signal_queue
            if not s.metadata.get("grid_level", "").startswith(level_str)
        )
        self._placed_levels.discard(level)
        self._filled_levels.pop(level, None)
        self._filled_entry_prices.pop(level, None)

    def release_level(self, grid_level: float) -> bool:
        """Compatibility fallback — no-op for this strategy."""
        return False

    def get_display_state(self) -> dict[str, object] | None:
        """Return strategy-specific state for UI display."""
        if self._anchor_price is None:
            return None

        # Show placed + filled levels and a few waiting levels around them
        active_levels = self._placed_levels | set(self._filled_levels.keys())
        all_display = sorted(set(self._grid_levels) | active_levels)

        # Window: find active range + 3 on each side
        active_indices = [i for i, lv in enumerate(all_display) if lv in active_levels]
        if active_indices:
            start = max(0, min(active_indices) - 3)
            end = min(len(all_display) - 1, max(active_indices) + 3)
        else:
            # Find levels nearest to anchor
            mid = len(all_display) // 2
            start = max(0, mid - 3)
            end = min(len(all_display) - 1, mid + 3)

        grid_levels: list[dict[str, object]] = []
        for i in range(start, end + 1):
            level = all_display[i]

            if level in self._filled_levels:
                pair_id = self._filled_levels[level]
                closed_sides = self._pair_closed_sides.get(pair_id, set())
                fills = self._filled_entry_prices.get(level, {})

                if "long" in closed_sides:
                    buy_status = "closed"
                elif "long" in fills:
                    buy_status = "active"
                else:
                    buy_status = "pending"

                if "short" in closed_sides:
                    sell_status = "closed"
                elif "short" in fills:
                    sell_status = "active"
                else:
                    sell_status = "pending"

                if "long" in closed_sides and "short" in closed_sides:
                    status = "closed"
                else:
                    status = "active"

                buy_fill = fills.get("long", 0.0)
                sell_fill = fills.get("short", 0.0)
                sd = self._stop_distance
                buy_sl = (buy_fill - sd) if buy_fill else level - sd
                sell_sl = (sell_fill + sd) if sell_fill else level + sd
            elif level in self._placed_levels:
                status = "placed"
                buy_status = "placed"
                sell_status = "placed"
                buy_fill = 0.0
                sell_fill = 0.0
                buy_sl = level - self._stop_distance
                sell_sl = level + self._stop_distance
            else:
                status = "waiting"
                buy_status = "none"
                sell_status = "none"
                buy_fill = 0.0
                sell_fill = 0.0
                buy_sl = 0.0
                sell_sl = 0.0

            grid_levels.append({
                "price": level,
                "status": status,
                "buy": {"status": buy_status, "fill": buy_fill, "sl": buy_sl},
                "sell": {"status": sell_status, "fill": sell_fill, "sl": sell_sl},
            })

        # Reverse so highest price is first
        grid_levels.reverse()

        return {
            "type": "paired_grid",
            "anchor_price": self._anchor_price,
            "grid_levels": grid_levels,
            "session_pnl": self._session_realized_pnl + self._session_unrealized_pnl,
            "session_profit_target": self._session_profit_target,
            "session_loss_limit": self._session_loss_limit,
            "daily_pnl": self._daily_realized_pnl + self._session_unrealized_pnl,
            "daily_loss_limit": self._daily_loss_limit,
            "session_count": self._session_count,
            "session_active": self._session_active,
            "filled_count": len(self._filled_levels),
            "placed_count": len(self._placed_levels),
            "session_history": list(self._session_history),
        }

    def _flat_close_all(self, bar: BarData, reason: str) -> Signal:
        """Create a FLAT signal requesting the engine close all positions."""
        return Signal(
            symbol=bar.symbol,
            signal_type=SignalType.FLAT,
            strategy_name=self.name,
            strength=1.0,
            metadata={"action": "close_all", "reason": reason},
        )

    def _trigger_close_all(self, reason: str) -> None:
        """Record session history and prepare for restart after close-all."""
        self._close_reason = reason
        self._session_history.append({
            "session": self._session_count,
            "reason": reason,
            "pnl": self._session_realized_pnl,
        })

    def notify_close_all_complete(self) -> None:
        """Called by engine after all positions are closed. Triggers session restart."""
        if self._session_active:
            self._restart_session()

    def _restart_session(self) -> None:
        """Reset session state for a fresh start. Preserves daily P&L and history."""
        self._anchor_price = None
        self._grid_levels = []
        self._signal_queue = deque()
        self._placed_levels = set()
        self._filled_levels = {}
        self._filled_entry_prices = {}
        self._pair_closed_sides = {}
        self._session_realized_pnl = 0.0
        self._session_unrealized_pnl = 0.0
        self._close_all_pending = False
        self._session_count += 1

    def _build_grid(self, anchor: float) -> list[float]:
        """Compute grid levels as multiples of grid_spacing around the anchor.

        Levels are rounded to nearest grid_spacing (e.g. spacing=10 → 4550, 4560, 4570).
        The anchor itself is NOT a level — levels are the nearest multiples above and below.
        """
        spacing = self._grid_spacing
        # First level above: ceil(anchor / spacing) * spacing
        first_above = math.ceil(anchor / spacing) * spacing
        # First level below: floor(anchor / spacing) * spacing
        first_below = math.floor(anchor / spacing) * spacing

        # If anchor is exactly on a grid line, don't duplicate
        levels: list[float] = []
        for i in range(100):
            above = round(first_above + i * spacing, 2)
            levels.append(above)
        for i in range(100):
            below = round(first_below - i * spacing, 2)
            if below not in levels:
                levels.append(below)

        return sorted(levels)

    def _place_initial_levels(self, bar: BarData) -> None:
        """Place limit orders at the first N levels above and below anchor."""
        self._symbol = bar.symbol  # Store for replenishment signals
        if self._anchor_price is None:
            return

        anchor = self._anchor_price
        spacing = self._grid_spacing

        # Find 2 levels above and 2 below
        first_above = math.ceil(anchor / spacing) * spacing
        first_below = math.floor(anchor / spacing) * spacing
        # If anchor is exactly on a grid line, first_above == first_below
        # Skip the current price level — a limit order there would fill immediately
        if first_above == first_below:
            first_above = round(first_above + spacing, 2)
            first_below = round(first_below - spacing, 2)

        levels_above = [round(first_above + i * spacing, 2) for i in range(self._LEVELS_AHEAD)]
        levels_below = [round(first_below - i * spacing, 2) for i in range(self._LEVELS_AHEAD)]

        for level in levels_above + levels_below:
            self._queue_limit_pair(bar, level)

    def _replenish_level(self, filled_level: float, _side: str) -> None:
        """After a level fills, place the next level beyond it."""
        if self._anchor_price is None:
            return

        # Determine direction: is the filled level above or below anchor?
        if filled_level > self._anchor_price:
            # Level was above — place the next one further above
            next_level = round(filled_level + self._grid_spacing, 2)
        else:
            # Level was below — place the next one further below
            next_level = round(filled_level - self._grid_spacing, 2)

        # Only place if not already placed or filled
        if next_level not in self._placed_levels and next_level not in self._filled_levels:
            # We need a bar for the signal symbol — use a placeholder
            # The signal just needs the symbol, actual price comes from limit_price
            self._queue_limit_pair_deferred(next_level)

    def _queue_limit_pair(self, bar: BarData, level: float) -> None:
        """Queue buy + sell limit order signals for a grid level (initial placement)."""
        self._queue_limit_pair_for_symbol(bar.symbol, level)

    def _queue_limit_pair_deferred(self, level: float) -> None:
        """Queue limit pair for replenishment (uses stored symbol)."""
        if not self._symbol:
            return
        self._queue_limit_pair_for_symbol(self._symbol, level)

    def _queue_limit_pair_for_symbol(self, symbol: str, level: float) -> None:
        """Queue buy + sell limit order signals for a grid level."""
        if level in self._placed_levels or level in self._filled_levels:
            return

        self._placed_levels.add(level)
        self._filled_entry_prices[level] = {}

        level_str = f"{level:.2f}"
        long_key = f"{level_str}_long"
        short_key = f"{level_str}_short"
        pair_id = str(uuid4())
        units = self._grid_units

        long_signal = Signal(
            symbol=symbol,
            signal_type=SignalType.LONG,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": long_key,
                "pair_id": pair_id,
                "pair_side": "long",
                "fixed_units": f"{units:.1f}",
                "order_type": "LIMIT",
                "limit_price": f"{level:.5f}",
                "entry_price": f"{level:.5f}",
            },
            stop_loss=level - self._stop_distance,
            take_profit=None,
        )

        short_signal = Signal(
            symbol=symbol,
            signal_type=SignalType.SHORT,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": short_key,
                "pair_id": pair_id,
                "pair_side": "short",
                "fixed_units": f"{units:.1f}",
                "order_type": "LIMIT",
                "limit_price": f"{level:.5f}",
                "entry_price": f"{level:.5f}",
            },
            stop_loss=level + self._stop_distance,
            take_profit=None,
        )

        self._signal_queue.append(long_signal)
        self._signal_queue.append(short_signal)
