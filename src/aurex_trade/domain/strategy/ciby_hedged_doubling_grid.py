"""Ciby Hedged Doubling Grid — breakout capture via hedged pairs + directional doubling.

Philosophy: do nothing in sideways markets, capture big directional moves, never bleed
from whipsaw. Hedged pairs at each level have NO stop loss (self-cancelling). Profit
comes exclusively from the doubled position at outer levels with a trailing stop.
"""

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

        # Level state tracking
        self._placed_levels: set[float] = set()  # Levels with pending limit orders
        self._placed_sides: dict[str, str] = {}  # grid_key → "buy"|"sell"
        self._filled_keys: dict[str, float] = {}  # grid_key → fill_price
        self._level_pair_complete: dict[float, set[str]] = {}  # level → {filled sides}

        # Doubling state
        self._doubled_level: float | None = None
        self._doubled_side: str = ""  # "long" or "short"
        self._doubled_grid_key: str = ""
        self._doubled_active: bool = False

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

        # Drain signal queue first
        if self._signal_queue:
            return self._signal_queue.popleft()

        if self._close_all_pending:
            self._close_all_pending = False
            return self._flat_close_all(current_bar, self._close_reason)

        if self._close_all_in_progress:
            return self._flat_close_all(current_bar, self._close_reason)

        if not self._session_active:
            return None

        # Check session loss limit
        if self._anchor_price is not None:
            total_pnl = self._session_realized_pnl + self._session_unrealized_pnl
            if total_pnl <= -self._session_loss_limit:
                self._trigger_close_all("session_loss_limit")
                return self._flat_close_all(current_bar, "session_loss_limit")

        # Check take-profit condition
        if (
            self._doubled_level is not None
            and self._doubled_active
            and self._check_take_profit(current_bar.close)
        ):
            self._trigger_close_all("take_profit")
            return self._flat_close_all(current_bar, "take_profit")

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
        """Called by engine when a trade fills at the broker.

        Tracks hedged pair completion and triggers doubling at outer levels.
        """
        self._filled_keys[grid_level_key] = fill_price

        # Parse level and side from key
        level, side = self._parse_grid_key(grid_level_key)
        if level is None:
            return

        # Track which sides have filled at this level
        if level not in self._level_pair_complete:
            self._level_pair_complete[level] = set()
        self._level_pair_complete[level].add(side)

        # Remove from placed tracking
        if side in ("buy", "sell"):
            self._placed_levels.discard(level)

        # Increment whipsaw counter for the level
        # Only count the first fill per level visit (buy side triggers the count)
        if side == "buy":
            self._level_trigger_counts[level] = (
                self._level_trigger_counts.get(level, 0) + 1
            )
            if self._level_trigger_counts[level] >= self._whipsaw_limit:
                self._session_paused = True
                self._close_reason = "whipsaw_pause"
                self._close_all_pending = True
                return

        # Check if this is an outer level with both sides filled → trigger doubling
        if self._doubled_level is None:
            self._check_doubling_trigger(level)

    def report_trade_closed(self, grid_level_key: str, realized_pnl: float) -> None:
        """Called by engine when a broker-side closure is detected."""
        self._session_realized_pnl += realized_pnl

        # If the doubled position closed (trailing stop hit), mark inactive
        if grid_level_key == self._doubled_grid_key:
            self._doubled_active = False

        # Remove from filled tracking
        self._filled_keys.pop(grid_level_key, None)

    def on_signal_rejected(self, grid_level_key: str) -> None:
        """Called by engine when risk rejects a signal."""
        level, _side = self._parse_grid_key(grid_level_key)
        if level is None:
            return

        # Remove queued signals for this key
        self._signal_queue = deque(
            s for s in self._signal_queue
            if s.metadata.get("grid_level") != grid_level_key
        )
        self._placed_levels.discard(level)
        self._placed_sides.pop(grid_level_key, None)

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
            buy_key = f"{level_str}_buy"
            sell_key = f"{level_str}_sell"
            doubled_key = f"{level_str}_doubled"

            buy_fill = self._filled_keys.get(buy_key, 0.0)
            sell_fill = self._filled_keys.get(sell_key, 0.0)

            filled_sides = self._level_pair_complete.get(level, set())

            if "buy" in filled_sides and "sell" in filled_sides:
                status = "active"
                buy_status = "active"
                sell_status = "active"
            elif level in self._placed_levels:
                status = "placed"
                buy_status = "active" if "buy" in filled_sides else "placed"
                sell_status = "active" if "sell" in filled_sides else "placed"
            else:
                status = "waiting"
                buy_status = "none"
                sell_status = "none"

            is_doubled = level == self._doubled_level
            doubled_info: dict[str, object] | None = None
            if is_doubled and self._doubled_active:
                doubled_info = {
                    "side": self._doubled_side,
                    "trailing_stop_distance": self._trailing_stop_distance,
                    "grid_key": doubled_key,
                }

            grid_levels.append({
                "price": level,
                "status": status,
                "buy": {"status": buy_status, "fill": buy_fill, "sl": "none"},
                "sell": {"status": sell_status, "fill": sell_fill, "sl": "none"},
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
        """Set anchor, compute 4 grid levels, and queue hedged pairs."""
        self._symbol = bar.symbol
        self._anchor_price = bar.close
        anchor = bar.close
        spacing = self._spacing

        # 2 levels above: inner (anchor + spacing), outer (anchor + 2*spacing)
        self._levels_above = [
            round(anchor + spacing, 2),
            round(anchor + 2 * spacing, 2),
        ]
        # 2 levels below: inner (anchor - spacing), outer (anchor - 2*spacing)
        self._levels_below = [
            round(anchor - spacing, 2),
            round(anchor - 2 * spacing, 2),
        ]

        # Place hedged pairs at all 4 levels
        for level in self._levels_above + self._levels_below:
            self._queue_hedged_pair(level)

    def _queue_hedged_pair(self, level: float) -> None:
        """Queue buy + sell limit orders at a level (hedged pair, no SL)."""
        if level in self._placed_levels:
            return

        # Skip if too close to current price
        if abs(level - self._current_price) < self._MIN_LIMIT_DISTANCE:
            return

        self._placed_levels.add(level)
        level_str = f"{level:.2f}"
        units = self._units

        # Buy limit
        buy_key = f"{level_str}_buy"
        buy_signal = Signal(
            symbol=self._symbol,
            signal_type=SignalType.LONG,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": buy_key,
                "pair_id": str(uuid4()),
                "fixed_units": f"{units:.1f}",
                "order_type": "LIMIT",
                "limit_price": f"{level:.5f}",
            },
            stop_loss=None,
            take_profit=None,
        )
        self._signal_queue.append(buy_signal)
        self._placed_sides[buy_key] = "buy"

        # Sell limit
        sell_key = f"{level_str}_sell"
        sell_signal = Signal(
            symbol=self._symbol,
            signal_type=SignalType.SHORT,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": sell_key,
                "pair_id": str(uuid4()),
                "fixed_units": f"{units:.1f}",
                "order_type": "LIMIT",
                "limit_price": f"{level:.5f}",
            },
            stop_loss=None,
            take_profit=None,
        )
        self._signal_queue.append(sell_signal)
        self._placed_sides[sell_key] = "sell"

    def _maintain_grid(self, bar: BarData) -> None:
        """Re-place levels that were cancelled or not yet placed."""
        for level in self._levels_above + self._levels_below:
            filled_sides = self._level_pair_complete.get(level, set())
            # If level has both sides filled, it's complete — no maintenance needed
            if "buy" in filled_sides and "sell" in filled_sides:
                continue
            # If not placed and not fully filled, try to place
            if level not in self._placed_levels:
                self._queue_hedged_pair(level)

    def _check_doubling_trigger(self, level: float) -> None:
        """Check if the filled level is an outer level and trigger doubling."""
        filled_sides = self._level_pair_complete.get(level, set())
        # Both sides must be filled to confirm price visited the level
        if "buy" not in filled_sides or "sell" not in filled_sides:
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

        doubled_signal = Signal(
            symbol=self._symbol,
            signal_type=signal_type,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": self._doubled_grid_key,
                "fixed_units": f"{self._units:.1f}",
                "order_type": "MARKET",
                "trailing_stop_distance": f"{self._trailing_stop_distance:.5f}",
            },
            stop_loss=None,
            take_profit=None,
        )
        self._signal_queue.append(doubled_signal)

    def _check_take_profit(self, current_price: float) -> bool:
        """Check if price has broken 2 levels beyond the doubled level."""
        if self._doubled_level is None or not self._doubled_active:
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
        self._placed_sides = {}
        self._filled_keys = {}
        self._level_pair_complete = {}
        self._doubled_level = None
        self._doubled_side = ""
        self._doubled_grid_key = ""
        self._doubled_active = False
        self._level_trigger_counts = {}
        self._session_paused = False
        self._session_realized_pnl = 0.0
        self._session_unrealized_pnl = 0.0
        self._close_all_pending = False
        self._close_all_in_progress = False

    def _parse_grid_key(self, grid_level_key: str) -> tuple[float | None, str]:
        """Parse a grid key like '4500.00_buy' into (level, side)."""
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return None, ""
        level_str, side = parts
        try:
            level = float(level_str)
        except ValueError:
            return None, ""
        return level, side
