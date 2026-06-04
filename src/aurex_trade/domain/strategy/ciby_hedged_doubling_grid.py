"""Ciby Hedged Doubling Grid — breakout capture via hedged pairs + directional doubling.

Philosophy: do nothing in sideways markets, capture big directional moves, never bleed
from whipsaw. Hedged pairs at each level have NO stop loss (self-cancelling). Profit
comes exclusively from the doubled position at outer levels with a trailing stop.
"""

import math
from collections import deque
from uuid import uuid4

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata


class CibyHedgedDoublingGridStrategy:
    """Breakout-capture grid strategy with hedged pairs and directional doubling.

    Setup:
    - Anchor at current price on start
    - 2 levels above + 2 below at fixed spacing
    - At each level: buy + sell limit (hedged pair, NO stop loss)
    - At outer levels (2nd from anchor): extra units on reversal side with trailing stop

    Profit mechanism:
    - Hedged pairs net to zero P&L (they mark that price visited a level)
    - Doubled position at outer level captures breakout/reversal moves
    - Trailing stop on doubled position locks in profit once +1 spacing

    Protection:
    - No SL on hedged pairs → no whipsaw bleeding
    - Trailing stop on doubled units → captures breakout, limits giveback
    - Session loss limit → circuit breaker
    - Whipsaw counter → pauses session after repeated re-triggers
    """

    _MIN_LIMIT_DISTANCE: float = 2.0

    def __init__(
        self,
        spacing: float = 20.0,
        units: float = 2.0,
        trailing_stop_distance: float = 20.0,
        session_loss_limit: float = 100.0,
        whipsaw_limit: int = 3,
    ) -> None:
        self._spacing = spacing
        self._units = units
        self._trailing_stop_distance = trailing_stop_distance
        self._session_loss_limit = session_loss_limit
        self._whipsaw_limit = whipsaw_limit

        # Session state
        self._symbol: str = ""
        self._anchor_price: float | None = None
        self._current_price: float = 0.0
        self._signal_queue: deque[Signal] = deque()

        # Grid structure: exactly 4 levels (2 above + 2 below anchor)
        self._levels_above: list[float] = []  # [inner_above, outer_above] ascending
        self._levels_below: list[float] = []  # [inner_below, outer_below] descending

        # Level state tracking (same pattern as v1)
        self._placed_levels: set[float] = set()  # Levels with pending limit orders
        self._placed_limit_side: dict[float, str] = {}  # level → "long"|"short"
        self._filled_levels: dict[float, str] = {}  # level → pair_id (active pairs)
        self._filled_entry_prices: dict[float, dict[str, float]] = {}  # level → {side: price}
        self._pair_closed_sides: dict[str, set[str]] = {}  # pair_id → {closed sides}

        # Doubling state
        self._doubled_level: float | None = None
        self._doubled_side: str = ""  # "long" or "short"
        self._doubled_grid_key: str = ""
        self._doubled_active: bool = False
        self._doubled_trailing_stop_set: bool = False
        self._doubled_has_broker_tp: bool = False

        # Whipsaw detection
        self._level_trigger_counts: dict[float, int] = {}
        self._session_paused: bool = False

        # P&L tracking
        self._session_realized_pnl: float = 0.0
        self._session_unrealized_pnl: float = 0.0

        # Close-all orchestration
        self._close_all_pending: bool = False
        self._close_all_in_progress: bool = False
        self._close_reason: str = ""
        self._session_active: bool = True

    @property
    def name(self) -> str:
        return "ciby_hedged_doubling_grid"

    @property
    def min_bars(self) -> int:
        return 1

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="Ciby Hedged Doubling Grid",
            description=(
                "A breakout-capture strategy that places hedged pairs (buy + sell) at "
                "grid levels with NO stop loss. Hedged pairs are self-cancelling and "
                "exist to mark that price visited a level. At outer levels, extra units "
                "are placed on the reversal side with a trailing stop — this is the only "
                "source of profit. Does nothing in sideways markets, captures big "
                "directional moves, never bleeds from whipsaw. Works best on volatile "
                "instruments like gold (XAU/USD)."
            ),
            params=(
                ParamMeta(
                    key="spacing",
                    label="Grid Spacing ($)",
                    tooltip=(
                        "Distance between grid levels in dollars. 2 levels above and 2 "
                        "below the anchor price are placed at this spacing. Wider spacing "
                        "means fewer false triggers but requires larger moves to profit."
                    ),
                    default=20.0,
                    min_value=5.0,
                    max_value=200.0,
                ),
                ParamMeta(
                    key="units",
                    label="Units per Level",
                    tooltip=(
                        "Position size for each side of a hedged pair. At outer levels, "
                        "the doubled position uses the same unit size (extra units added "
                        "on top of the hedged pair)."
                    ),
                    default=2.0,
                    min_value=1.0,
                    max_value=50.0,
                ),
                ParamMeta(
                    key="trailing_stop_distance",
                    label="Trailing Stop Distance ($)",
                    tooltip=(
                        "Trail distance on the doubled position. The trailing stop "
                        "activates immediately at placement and trails at this distance. "
                        "OANDA manages the trail server-side."
                    ),
                    default=20.0,
                    min_value=1.0,
                    max_value=200.0,
                ),
                ParamMeta(
                    key="session_loss_limit",
                    label="Session Loss Limit ($)",
                    tooltip=(
                        "Maximum session loss before closing all positions and pausing. "
                        "This is the circuit breaker for adverse continuation scenarios "
                        "where the doubled position goes against you."
                    ),
                    default=100.0,
                    min_value=10.0,
                    max_value=10000.0,
                ),
                ParamMeta(
                    key="whipsaw_limit",
                    label="Whipsaw Limit",
                    tooltip=(
                        "Number of times the same level can re-trigger before pausing "
                        "the session. Prevents excessive activity in choppy markets."
                    ),
                    default=3.0,
                    min_value=1.0,
                    max_value=10.0,
                ),
            ),
        )

    def update_unrealized_pnl(self, unrealized_pnl: float) -> None:
        """Called by engine each cycle with current unrealized P&L from broker."""
        self._session_unrealized_pnl = unrealized_pnl

    def generate(self, bars: list[BarData]) -> Signal | None:
        """Generate signals: drain queue, check exits, place levels, check take-profit."""
        if not bars:
            return None

        current_bar = bars[-1]
        self._current_price = current_bar.close

        if self._close_all_pending:
            self._close_all_pending = False
            self._signal_queue.clear()
            return self._flat_close_all(current_bar, self._close_reason)

        if self._close_all_in_progress:
            return self._flat_close_all(current_bar, self._close_reason)

        if not self._session_active:
            return None

        # Check session loss limit BEFORE draining queue (prevents opening
        # new positions when session should be shutting down)
        if self._anchor_price is not None:
            total_pnl = self._session_realized_pnl + self._session_unrealized_pnl
            if total_pnl <= -self._session_loss_limit:
                self._signal_queue.clear()
                self._trigger_close_all("session_loss_limit")
                return self._flat_close_all(current_bar, "session_loss_limit")

        # Check take-profit condition
        if (
            self._doubled_level is not None
            and self._doubled_active
            and self._check_take_profit(current_bar.close)
        ):
            self._signal_queue.clear()
            self._trigger_close_all("take_profit")
            return self._flat_close_all(current_bar, "take_profit")

        # Drain signal queue (after safety checks pass)
        if self._signal_queue:
            return self._signal_queue.popleft()

        # Initialize: anchor + build grid + place hedged pairs
        if self._anchor_price is None:
            self._initialize_grid(current_bar)
            if self._signal_queue:
                return self._signal_queue.popleft()

        # Maintenance: re-place cancelled levels
        if self._anchor_price is not None:
            self._maintain_grid(current_bar)
            if self._signal_queue:
                return self._signal_queue.popleft()

        return None

    def report_fill(self, grid_level_key: str, fill_price: float) -> None:
        """Called by engine when a trade fills (limit or opposite market).

        First fill at a level (the limit side) transitions it from placed → active.
        Second fill (the opposite market side) records the entry price.
        Once both sides fill, check for doubling trigger at outer levels.
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
        else:
            self._filled_entry_prices[level] = {side: fill_price}

        # First fill at this level: limit side fills, mark active
        if level in self._placed_levels and level not in self._filled_levels:
            pair_id = str(uuid4())
            self._filled_levels[level] = pair_id
            self._placed_levels.discard(level)
            self._placed_limit_side.pop(level, None)

            # Increment whipsaw counter on first fill
            self._level_trigger_counts[level] = (
                self._level_trigger_counts.get(level, 0) + 1
            )
            if self._level_trigger_counts[level] >= self._whipsaw_limit:
                self._session_paused = True
                self._close_reason = "whipsaw_pause"
                self._close_all_pending = True
                return

        # Check if both sides are now filled → trigger doubling at outer levels
        fills = self._filled_entry_prices.get(level, {})
        if "long" in fills and "short" in fills and self._doubled_level is None:
            self._check_doubling_trigger(level)

    def report_trade_closed(self, grid_level_key: str, realized_pnl: float) -> None:
        """Called by engine when a broker-side closure is detected."""
        self._session_realized_pnl += realized_pnl

        # If the doubled position closed (trailing stop hit), close all and restart
        if grid_level_key == self._doubled_grid_key:
            self._doubled_active = False
            self._close_reason = "doubled_closed"
            self._close_all_pending = True
            return

        # Track which sides of a pair have closed
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

        # Release level when both sides close
        if len(self._pair_closed_sides[pair_id]) >= 2:
            del self._filled_levels[level]
            del self._pair_closed_sides[pair_id]
            self._filled_entry_prices.pop(level, None)

    def on_signal_rejected(self, grid_level_key: str) -> None:
        """Called by engine when risk rejects a signal."""
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return
        level_str, _side = parts
        try:
            level = float(level_str)
        except ValueError:
            return

        # Remove queued signals for this level
        self._signal_queue = deque(
            s for s in self._signal_queue
            if not s.metadata.get("grid_level", "").startswith(level_str)
        )
        self._placed_levels.discard(level)
        self._placed_limit_side.pop(level, None)
        self._filled_levels.pop(level, None)
        self._filled_entry_prices.pop(level, None)

    def get_deferred_trailing_stop(self) -> dict[str, object] | None:
        """Return trailing stop config if doubled position needs activation.

        The engine calls this each fast poll. Returns None if no action needed,
        or a dict with grid_key, distance, and activation_profit if the trailing
        stop should be set once the position is sufficiently in profit.
        """
        if (
            not self._doubled_active
            or self._doubled_trailing_stop_set
            or not self._doubled_grid_key
        ):
            return None
        return {
            "grid_key": self._doubled_grid_key,
            "side": self._doubled_side,
            "distance": self._trailing_stop_distance,
            "activation_profit": self._spacing,
        }

    def notify_trailing_stop_set(self) -> None:
        """Called by engine after trailing stop is successfully set on the doubled position."""
        self._doubled_trailing_stop_set = True

    def release_level(self, grid_level: float) -> bool:
        """Compatibility — no-op."""
        return False

    def notify_close_all_complete(self) -> None:
        """Called by engine after all positions are closed."""
        self._close_all_in_progress = False
        # If paused due to whipsaw, stay stopped
        if self._session_paused:
            self._session_active = False
        elif self._session_active:
            self._restart_session()

    def get_display_state(self) -> dict[str, object] | None:
        """Return strategy-specific state for UI display."""
        if self._anchor_price is None:
            return None

        all_levels = sorted(self._levels_above + self._levels_below, reverse=True)
        grid_levels: list[dict[str, object]] = []

        for level in all_levels:
            level_str = f"{level:.2f}"
            doubled_key = f"{level_str}_doubled"
            fills = self._filled_entry_prices.get(level, {})

            # Determine which side was limit vs market
            limit_side = self._placed_limit_side.get(level, "")
            if not limit_side and level in self._filled_levels:
                # Already filled — infer from entry prices
                # The limit side fills at level price, market side fills at spread
                limit_side = "short" if level > (self._anchor_price or 0) else "long"

            if level in self._filled_levels:
                pair_id = self._filled_levels[level]
                closed_sides = self._pair_closed_sides.get(pair_id, set())

                buy_status = "closed" if "long" in closed_sides else (
                    "active" if "long" in fills else "placed"
                )
                sell_status = "closed" if "short" in closed_sides else (
                    "active" if "short" in fills else "placed"
                )
                both_closed = "long" in closed_sides and "short" in closed_sides
                status = "closed" if both_closed else "active"
                buy_fill = fills.get("long", 0.0)
                sell_fill = fills.get("short", 0.0)
            elif level in self._placed_levels:
                status = "placed"
                buy_status = "placed" if limit_side == "long" else "waiting"
                sell_status = "placed" if limit_side == "short" else "waiting"
                buy_fill = 0.0
                sell_fill = 0.0
            else:
                status = "waiting"
                buy_status = "none"
                sell_status = "none"
                buy_fill = 0.0
                sell_fill = 0.0

            buy_order_type = "limit" if limit_side == "long" else "market"
            sell_order_type = "limit" if limit_side == "short" else "market"

            is_outer = (
                (level in self._levels_above and level == self._levels_above[-1])
                or (level in self._levels_below and level == self._levels_below[-1])
            )

            is_doubled = level == self._doubled_level
            doubled_info: dict[str, object] | None = None
            if is_doubled and self._doubled_active:
                doubled_info = {
                    "side": self._doubled_side,
                    "trailing_stop_distance": self._trailing_stop_distance,
                    "grid_key": doubled_key,
                }

            tp_distance = 2 * self._spacing
            buy_tp = round(level + tp_distance, 2) if buy_fill else round(level + tp_distance, 2)
            sell_tp = round(level - tp_distance, 2) if sell_fill else round(level - tp_distance, 2)

            grid_levels.append({
                "price": level,
                "status": status,
                "is_outer": is_outer,
                "buy": {
                    "status": buy_status,
                    "fill": buy_fill,
                    "sl": "none",
                    "tp": buy_tp,
                    "order_type": buy_order_type,
                    "units": self._units,
                },
                "sell": {
                    "status": sell_status,
                    "fill": sell_fill,
                    "sl": "none",
                    "tp": sell_tp,
                    "order_type": sell_order_type,
                    "units": self._units,
                },
                "doubled": doubled_info,
            })

        max_whipsaw = max(self._level_trigger_counts.values()) if self._level_trigger_counts else 0

        return {
            "type": "doubled_grid",
            "anchor_price": self._anchor_price,
            "current_price": self._current_price,
            "grid_levels": grid_levels,
            "session_pnl": self._session_realized_pnl + self._session_unrealized_pnl,
            "session_loss_limit": self._session_loss_limit,
            "doubled_level": self._doubled_level,
            "doubled_side": self._doubled_side,
            "doubled_active": self._doubled_active,
            "whipsaw_count": max_whipsaw,
            "whipsaw_limit": self._whipsaw_limit,
            "session_paused": self._session_paused,
            "trailing_stop_distance": self._trailing_stop_distance,
        }

    # --- Private helpers ---

    def _initialize_grid(self, bar: BarData) -> None:
        """Set anchor, compute 4 grid levels at round multiples, and queue limits."""
        self._symbol = bar.symbol
        self._anchor_price = bar.close
        anchor = bar.close
        spacing = self._spacing

        # Levels at round multiples of spacing (same approach as v1)
        first_above = math.ceil(anchor / spacing) * spacing
        first_below = math.floor(anchor / spacing) * spacing

        # If anchor is exactly on a grid line, skip it
        if first_above == first_below:
            first_above = round(first_above + spacing, 2)
            first_below = round(first_below - spacing, 2)

        # 2 levels above (ascending), 2 levels below (descending)
        self._levels_above = [
            round(first_above, 2),
            round(first_above + spacing, 2),
        ]
        self._levels_below = [
            round(first_below, 2),
            round(first_below - spacing, 2),
        ]

        # Place ONE limit per level (engine places opposite on fill)
        for level in self._levels_above + self._levels_below:
            self._queue_limit_for_level(level)

    def _queue_limit_for_level(self, level: float) -> None:
        """Queue ONE limit order for a grid level (the side that waits).

        If current price is below the level → place SELL LIMIT (waits for rise).
        If current price is above the level → place BUY LIMIT (waits for drop).
        On fill, the engine places the opposite side as a market order (no SL).
        """
        if level in self._placed_levels or level in self._filled_levels:
            return

        if abs(level - self._current_price) < self._MIN_LIMIT_DISTANCE:
            return

        self._placed_levels.add(level)
        self._filled_entry_prices[level] = {}

        level_str = f"{level:.2f}"
        pair_id = str(uuid4())
        units = self._units

        if self._current_price < level:
            # Price below level → sell limit waits for price to rise
            limit_side = "short"
            opposite_side = "long"
            signal_type = SignalType.SHORT
        else:
            # Price above level → buy limit waits for price to drop
            limit_side = "long"
            opposite_side = "short"
            signal_type = SignalType.LONG

        grid_key = f"{level_str}_{limit_side}"
        opposite_grid_key = f"{level_str}_{opposite_side}"

        self._placed_limit_side[level] = limit_side

        tp_distance = 2 * self._spacing
        if signal_type == SignalType.LONG:
            take_profit = round(level + tp_distance, 5)
        else:
            take_profit = round(level - tp_distance, 5)

        if opposite_side == "long":
            opposite_tp = round(level + tp_distance, 5)
        else:
            opposite_tp = round(level - tp_distance, 5)

        signal = Signal(
            symbol=self._symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": grid_key,
                "pair_id": pair_id,
                "pair_side": limit_side,
                "fixed_units": f"{units:.1f}",
                "order_type": "LIMIT",
                "limit_price": f"{level:.5f}",
                "entry_price": f"{level:.5f}",
                "opposite_side": "BUY" if opposite_side == "long" else "SELL",
                "opposite_grid_level": opposite_grid_key,
                "opposite_stop_loss": "",
                "opposite_take_profit": f"{opposite_tp:.5f}",
            },
            stop_loss=None,
            take_profit=take_profit,
        )

        self._signal_queue.append(signal)

    def _maintain_grid(self, bar: BarData) -> None:
        """Re-place levels that were cancelled or not yet placed."""
        for level in self._levels_above + self._levels_below:
            if level not in self._placed_levels and level not in self._filled_levels:
                self._queue_limit_for_level(level)

    def _check_doubling_trigger(self, level: float) -> None:
        """Check if the filled level is an outer level and trigger doubling."""
        fills = self._filled_entry_prices.get(level, {})
        # Both sides must be filled to confirm price visited the level
        if "long" not in fills or "short" not in fills:
            return

        # Check if this is an outer level (2nd from anchor)
        is_outer_above = level in self._levels_above and level == self._levels_above[-1]
        is_outer_below = level in self._levels_below and level == self._levels_below[-1]

        if not is_outer_above and not is_outer_below:
            return

        # Trigger doubling
        self._doubled_level = level
        level_str = f"{level:.2f}"
        self._doubled_grid_key = f"{level_str}_doubled"

        if is_outer_below:
            # Price dropped to outer below → extra BUY (betting on bounce)
            self._doubled_side = "long"
            signal_type = SignalType.LONG
        else:
            # Price rose to outer above → extra SELL (betting on reversal)
            self._doubled_side = "short"
            signal_type = SignalType.SHORT

        self._doubled_active = True

        tp_distance = 2 * self._spacing
        if self._doubled_side == "long":
            doubled_tp = round(level + tp_distance, 5)
        else:
            doubled_tp = round(level - tp_distance, 5)

        doubled_signal = Signal(
            symbol=self._symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": self._doubled_grid_key,
                "fixed_units": f"{self._units:.1f}",
                "order_type": "MARKET",
            },
            stop_loss=None,
            take_profit=doubled_tp,
        )
        self._signal_queue.append(doubled_signal)
        self._doubled_has_broker_tp = True

    def _check_take_profit(self, current_price: float) -> bool:
        """Check if price has broken 2 levels beyond the doubled level."""
        if self._doubled_level is None or not self._doubled_active:
            return False
        if self._doubled_has_broker_tp:
            return False

        target_distance = 2 * self._spacing

        if self._doubled_side == "long":
            # Doubled buy at outer below → profit when price rises
            # Take profit when price is 2 spacings above the doubled level
            return current_price >= self._doubled_level + target_distance
        # Doubled sell at outer above → profit when price drops
        # Take profit when price is 2 spacings below the doubled level
        return current_price <= self._doubled_level - target_distance

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
        """Prepare for close-all."""
        self._close_reason = reason
        self._close_all_in_progress = True

    def _restart_session(self) -> None:
        """Reset session state for a fresh start."""
        self._anchor_price = None
        self._levels_above = []
        self._levels_below = []
        self._signal_queue = deque()
        self._placed_levels = set()
        self._placed_limit_side = {}
        self._filled_levels = {}
        self._filled_entry_prices = {}
        self._pair_closed_sides = {}
        self._doubled_level = None
        self._doubled_side = ""
        self._doubled_grid_key = ""
        self._doubled_active = False
        self._doubled_trailing_stop_set = False
        self._doubled_has_broker_tp = False
        self._level_trigger_counts = {}
        self._session_paused = False
        self._session_realized_pnl = 0.0
        self._session_unrealized_pnl = 0.0
        self._close_all_pending = False
        self._close_all_in_progress = False

