"""Ciby Hedged Grid strategy — hedged pairs at grid levels with session P&L exits."""

from collections import deque
from uuid import uuid4

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata


class CibyHedgedGridStrategy:
    """Directional-agnostic grid strategy that opens hedged pairs (buy + sell).

    At each grid level crossing, places a buy AND a sell simultaneously. Profits
    from sustained directional movement where winning sides accumulate while
    losing sides are capped by stops.

    Risk is managed via:
    - Session profit target: close all & restart fresh
    - Session loss limit: close all & restart fresh
    - Daily loss limit: close all & stop trading for the day
    """

    def __init__(
        self,
        grid_spacing: float = 15.0,
        initial_units: float = 10.0,
        grid_units: float = 20.0,
        stop_distance: float = 16.0,
        session_profit_target: float = 100.0,
        session_loss_limit: float = 50.0,
        daily_loss_limit: float = 200.0,
    ) -> None:
        self._grid_spacing = grid_spacing
        self._initial_units = initial_units
        self._grid_units = grid_units
        self._stop_distance = stop_distance
        self._session_profit_target = session_profit_target
        self._session_loss_limit = session_loss_limit
        self._daily_loss_limit = daily_loss_limit

        # Mutable session state
        self._anchor_price: float | None = None
        self._grid_levels: list[float] = []
        self._signal_queue: deque[Signal] = deque()
        self._filled_levels: dict[float, str] = {}
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
        return 2

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="Ciby Hedged Grid",
            description=(
                "A grid trading strategy developed by legendary gold trader Ciby. "
                "A directional-agnostic grid strategy that opens "
                "hedged pairs (buy + sell) at each grid level. Profits from sustained "
                "directional movement where winning sides accumulate while losing sides "
                "are capped by stop-losses. In sideways markets within one grid band, "
                "no stops are hit — zero cost optionality. Risk is managed via session "
                "profit targets (close all and restart), session loss limits, and daily "
                "loss limits. Works best on volatile instruments like gold (XAU/USD)."
            ),
            params=(
                ParamMeta(
                    key="grid_spacing",
                    label="Grid Spacing ($)",
                    tooltip=(
                        "Distance between grid levels in dollars. Every time price moves "
                        "this far from the anchor, a new hedged pair is placed. For gold, "
                        "15 is typical. Smaller = more pairs but higher stop risk."
                    ),
                    default=15.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="initial_units",
                    label="Initial Units",
                    tooltip=(
                        "Position size for the first hedged pair placed at the anchor "
                        "price when a session starts. Smaller than grid units to limit "
                        "risk at uncertain entry."
                    ),
                    default=10.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="grid_units",
                    label="Grid Units",
                    tooltip=(
                        "Position size for subsequent hedged pairs at grid levels. "
                        "Larger than initial units because grid crossings confirm "
                        "directional momentum."
                    ),
                    default=20.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="stop_distance",
                    label="Stop Distance ($)",
                    tooltip=(
                        "Stop-loss distance from entry price in dollars. Should be "
                        "slightly larger than grid_spacing so stops sit just past the "
                        "adjacent grid level. For 15-point grid, 16 is typical."
                    ),
                    default=16.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="session_profit_target",
                    label="Session Profit Target ($)",
                    tooltip=(
                        "When total session P&L (realized from closed trades) reaches "
                        "this target, close all positions and restart fresh at current "
                        "price. Locks in gains before a reversal can erode them."
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
                        "close all positions and restart fresh. Caps session damage from "
                        "repeated whipsaw."
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
        """Generate a signal: drain queue, check P&L exits, or detect grid crossings."""
        if len(bars) < self.min_bars:
            return None

        current_bar = bars[-1]
        current_date = current_bar.timestamp.strftime("%Y-%m-%d")

        # Day boundary reset — new day, fresh start
        if self._current_date and current_date != self._current_date:
            self._daily_realized_pnl = 0.0
            self._session_active = True
            self._session_count = 1
            self._session_history = []
            self._restart_session()
        self._current_date = current_date

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

        # Initialize session — place initial pair at anchor
        if self._anchor_price is None:
            self._anchor_price = current_bar.close
            self._grid_levels = self._build_grid(current_bar.close)
            return self._create_pair_signals(current_bar, current_bar.close, is_initial=True)

        # Detect grid level crossings
        current_price = current_bar.close
        prev_price = bars[-2].close

        for level in self._grid_levels:
            if level in self._filled_levels:
                continue

            crossed_up = prev_price < level <= current_price
            crossed_down = prev_price > level >= current_price
            if crossed_up or crossed_down:
                return self._create_pair_signals(current_bar, level, is_initial=False)

        return None

    def report_fill(self, grid_level_key: str, fill_price: float) -> None:
        """Called by engine when a trade is filled at the broker.

        Stores the actual fill price per side (long/short) so SL display
        and calculations use real broker prices, not signal-generation estimates.
        """
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return
        level_str, side = parts
        try:
            level = float(level_str)
        except ValueError:
            return

        if level in self._filled_entry_prices:
            self._filled_entry_prices[level][side] = fill_price

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

    def on_signal_rejected(self, grid_level_key: str) -> None:
        """Called by engine when risk rejects a signal from this strategy.

        If the rejected signal was the FIRST of a pair (partner still queued),
        clear the queued partner and release the filled level.
        If it was the SECOND (partner already executed), keep the level filled
        and mark the rejected side as closed so the level can release when the
        surviving trade closes.
        """
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return
        level_str, side = parts
        try:
            level = float(level_str)
        except ValueError:
            return

        # Check if the queued partner still exists (i.e., first signal rejected)
        old_len = len(self._signal_queue)
        self._signal_queue = deque(
            s for s in self._signal_queue
            if not s.metadata.get("grid_level", "").startswith(level_str)
        )
        partner_was_queued = len(self._signal_queue) < old_len

        if level not in self._filled_levels:
            return

        if partner_was_queued:
            # First signal rejected — partner never executed. Release fully.
            pair_id = self._filled_levels.pop(level)
            self._pair_closed_sides.pop(pair_id, None)
        else:
            # Second signal rejected — partner already executed.
            # Mark the rejected side as closed so level releases when partner closes.
            pair_id = self._filled_levels[level]
            if pair_id not in self._pair_closed_sides:
                self._pair_closed_sides[pair_id] = set()
            self._pair_closed_sides[pair_id].add(side)

    def release_level(self, grid_level: float) -> bool:
        """Compatibility with engine's existing release_level dispatch.

        For this strategy, actual release logic is in report_trade_closed
        and on_signal_rejected. This is a no-op fallback.
        """
        return False

    def get_display_state(self) -> dict[str, object] | None:
        """Return strategy-specific state for UI display."""
        if self._anchor_price is None:
            return None

        # Include anchor price in display (initial pair is placed there)
        all_levels = sorted(set(self._grid_levels) | {self._anchor_price})

        grid_levels: list[dict[str, object]] = []
        for level in reversed(all_levels):
            if level in self._filled_levels:
                status = "active"
                pair_id = self._filled_levels[level]
                closed_sides = self._pair_closed_sides.get(pair_id, set())
                buy_status = "stopped" if "long" in closed_sides else "active"
                sell_status = "stopped" if "short" in closed_sides else "active"
                if "long" in closed_sides and "short" in closed_sides:
                    status = "closed"
                fills = self._filled_entry_prices.get(level, {})
                buy_fill = fills.get("long", level)
                sell_fill = fills.get("short", level)
                buy_sl = buy_fill - self._stop_distance
                sell_sl = sell_fill + self._stop_distance
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

        return {
            "type": "paired_grid",
            "anchor_price": self._anchor_price,
            "grid_levels": grid_levels,
            "session_pnl": self._session_realized_pnl + self._session_unrealized_pnl,
            "session_profit_target": self._session_profit_target,
            "session_loss_limit": self._session_loss_limit,
            "daily_pnl": self._daily_realized_pnl,
            "daily_loss_limit": self._daily_loss_limit,
            "session_count": self._session_count,
            "session_active": self._session_active,
            "filled_count": len(self._filled_levels),
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
        self._filled_levels = {}
        self._filled_entry_prices = {}
        self._pair_closed_sides = {}
        self._session_realized_pnl = 0.0
        self._session_unrealized_pnl = 0.0
        self._close_all_pending = False
        self._session_count += 1

    def _build_grid(self, anchor: float) -> list[float]:
        """Compute symmetric grid levels around the anchor price.

        Uses 100 levels each side to allow unlimited trending without running
        out of grid. At 15-point spacing, covers ±1500 points — far beyond
        any realistic session move for gold.
        """
        num_levels = 100
        levels: list[float] = []
        for i in range(1, num_levels + 1):
            levels.append(round(anchor - i * self._grid_spacing, 2))
            levels.append(round(anchor + i * self._grid_spacing, 2))
        return sorted(levels)

    def _create_pair_signals(
        self, bar: BarData, level: float, *, is_initial: bool
    ) -> Signal:
        """Create a hedged pair (LONG + SHORT) at the given level.

        Returns the first signal immediately; queues the second.
        Marks the level as filled.
        """
        units = self._initial_units if is_initial else self._grid_units
        pair_id = str(uuid4())
        entry_price = bar.close

        self._filled_levels[level] = pair_id
        self._filled_entry_prices[level] = {"long": entry_price, "short": entry_price}

        level_str = f"{level:.2f}"
        long_key = f"{level_str}_long"
        short_key = f"{level_str}_short"

        long_signal = Signal(
            symbol=bar.symbol,
            signal_type=SignalType.LONG,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": long_key,
                "anchor_price": f"{self._anchor_price:.2f}" if self._anchor_price else "",
                "pair_id": pair_id,
                "pair_side": "long",
                "fixed_units": f"{units:.1f}",
                "entry_price": f"{entry_price:.5f}",
            },
            stop_loss=entry_price - self._stop_distance,
            take_profit=None,
        )

        short_signal = Signal(
            symbol=bar.symbol,
            signal_type=SignalType.SHORT,
            strategy_name=self.name,
            strength=1.0,
            metadata={
                "grid_level": short_key,
                "anchor_price": f"{self._anchor_price:.2f}" if self._anchor_price else "",
                "pair_id": pair_id,
                "pair_side": "short",
                "fixed_units": f"{units:.1f}",
                "entry_price": f"{entry_price:.5f}",
            },
            stop_loss=entry_price + self._stop_distance,
            take_profit=None,
        )

        self._signal_queue.append(short_signal)
        return long_signal
