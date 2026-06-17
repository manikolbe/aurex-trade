"""Ciby Sliding Grid — a fixed window of hedged pairs that slides with price.

Setup:
- Anchor at the market price on start; place a hedged pair (buy + sell) there.
- The first level above and below the anchor sits ``anchor_gap`` away; every
  level beyond that is ``grid_spacing`` apart. So with anchor 4100, anchor_gap 15
  and spacing 10 the ladder runs ...4075, 4085, 4100(anchor), 4115, 4125...
- At each level a hedged pair rests: a SELL at the level price and a BUY
  ``buy_sell_offset`` above it (to work around the spread).
- Each side carries a stop just past the adjacent level in its losing direction
  (a buy is stopped below, a sell above): SL distance = gap-to-next-level + buffer.

Placement uses resting LIMIT and STOP entry orders so each side fills at its exact
price regardless of which way price approaches:
- Level ABOVE current price: SELL rests as a LIMIT; BUY rests as a STOP.
- Level BELOW current price: BUY rests as a LIMIT; SELL rests as a STOP.
A plain limit on the breakout side would fill instantly ("at this price or
better"), so the breakout side uses a STOP instead.

The grid SLIDES rather than grows: only a small, fixed window of levels stays
active — ``max_levels_ahead`` on the side price is trending into and
``max_levels_behind`` on the trailing side. As price advances and a further level
opens, the trailing level nearest the anchor is closed to free margin (banking its
result). The ANCHOR pair is exempt — it is never trimmed, so its winning side rides
the trend. That anchor leg is the real source of profit; the trimmed hedged pairs
close at or near break-even and exist only to keep the bot positioned without
running out of margin. The window flips direction with price.

Risk is managed via session profit target, session loss limit, and daily loss
limit — each triggers a close-all (and the session restarts fresh, except the
daily limit which stops trading until the next day).
"""

from collections import deque

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata


class CibySlidingGridStrategy:
    """Directional-agnostic grid: a fixed window of hedged pairs slides with price."""

    _LEVELS_AHEAD = 2  # Unfilled levels to keep resting in each direction
    # Minimum distance from current price to rest an order. Closer than this and
    # the order would fill immediately or be rejected by the broker.
    _MIN_RESTING_DISTANCE: float = 2.0

    def __init__(
        self,
        grid_spacing: float = 10.0,
        anchor_gap: float = 15.0,
        buy_sell_offset: float = 0.90,
        anchor_units: float = 10.0,
        grid_units: float = 20.0,
        stop_buffer: float = 1.0,
        max_levels_ahead: int = 2,
        max_levels_behind: int = 1,
        session_profit_target: float = 100.0,
        session_loss_limit: float = 50.0,
        daily_loss_limit: float = 200.0,
    ) -> None:
        self._grid_spacing = grid_spacing
        self._anchor_gap = anchor_gap
        self._buy_sell_offset = buy_sell_offset
        self._anchor_units = anchor_units
        self._grid_units = grid_units
        self._stop_buffer = stop_buffer
        # Active-level caps relative to the anchor, by direction of travel. The
        # side price is moving INTO keeps up to max_levels_ahead active levels;
        # the trailing side keeps max_levels_behind. The anchor is exempt — it is
        # never trimmed, and its winning side rides the trend (the profit source).
        self._max_levels_ahead = max_levels_ahead
        self._max_levels_behind = max_levels_behind
        self._session_profit_target = session_profit_target
        self._session_loss_limit = session_loss_limit
        self._daily_loss_limit = daily_loss_limit

        # Mutable session state
        self._symbol: str = ""
        self._anchor_price: float | None = None
        self._current_price: float = 0.0
        self._levels: list[float] = []  # Sorted ladder of level prices (the SELL price)
        self._signal_queue: deque[Signal] = deque()

        # Per-level, per-side state. Side keys are "long" (buy) and "short" (sell).
        # A level is "open" once at least one side has rested; a side is tracked in
        # _placed (resting), _filled (active trade), or _stopped (closed out).
        self._placed: dict[float, set[str]] = {}  # level → sides resting at broker
        self._filled: dict[float, set[str]] = {}  # level → sides with active trades
        self._stopped: dict[float, set[str]] = {}  # level → sides closed (SL hit)
        self._fill_prices: dict[float, dict[str, float]] = {}  # level → {side: price}
        # Order type each side was entered as (MARKET/LIMIT/STOP), kept for display
        # so a filled level shows how it was actually placed, not a recompute.
        self._order_types: dict[str, str] = {}  # grid_key → order type

        # Margin management: levels the strategy has decided to retire (trim) to
        # stay within the active-level caps. _pending_close holds grid keys handed
        # to the engine for closing; _retired holds levels already trimmed so a
        # revisit does NOT re-open them (distinguishes a deliberate trim from an
        # SL hit, which would otherwise re-place the side).
        self._pending_close: set[str] = set()  # grid keys awaiting engine close
        self._retired: set[float] = set()  # levels trimmed for margin — do not re-open

        # P&L tracking
        self._session_realized_pnl: float = 0.0
        self._session_unrealized_pnl: float = 0.0
        self._daily_realized_pnl: float = 0.0

        # Session / day orchestration
        self._session_active: bool = True
        self._current_date: str = ""
        self._close_all_pending: bool = False
        self._close_all_in_progress: bool = False
        self._close_reason: str = ""
        self._session_count: int = 1
        self._session_history: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "ciby_sliding_grid"

    @property
    def min_bars(self) -> int:
        return 1

    @classmethod
    def metadata(cls) -> StrategyMetadata:
        return StrategyMetadata(
            display_name="Ciby Sliding Grid",
            description=(
                "A grid hedging strategy where a small, fixed window of levels slides "
                "with price. Anchors at the starting price with a hedged pair (buy + "
                "sell), then places further hedged pairs at fixed intervals above and "
                "below — the first level a wider 'anchor gap' away, every level beyond "
                "that one grid spacing apart. Each side rests at its exact price (using "
                "stop and limit entry orders) with a stop-loss just past the next level "
                "in its losing direction. Only a capped number of levels stay active; "
                "as price trends, trailing levels are closed to free margin while the "
                "anchor pair stays open and rides the move — the main source of profit. "
                "Risk is managed via session profit target, session loss limit, and "
                "daily loss limit. Works best on volatile instruments like gold "
                "(XAU/USD)."
            ),
            params=(
                ParamMeta(
                    key="grid_spacing",
                    label="Grid Spacing ($)",
                    tooltip=(
                        "Distance between consecutive grid levels beyond the first. "
                        "After the anchor gap, every level is this far from the previous "
                        "one (e.g. spacing=10 → 4115, 4125, 4135...)."
                    ),
                    default=10.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="anchor_gap",
                    label="Anchor Gap ($)",
                    tooltip=(
                        "Distance from the anchor to the first level above and below it. "
                        "Typically wider than the grid spacing to give the first pair "
                        "more room (e.g. anchor 4100, gap 15 → first levels at 4085 and "
                        "4115)."
                    ),
                    default=15.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="buy_sell_offset",
                    label="Buy/Sell Offset ($)",
                    tooltip=(
                        "Gap between the buy and sell of a hedged pair to work around "
                        "the spread. The sell rests at the level price; the buy rests "
                        "this much above it (e.g. offset 0.90 → sell 4100.00, buy "
                        "4100.90)."
                    ),
                    default=0.90,
                    min_value=0.0,
                    max_value=10.0,
                ),
                ParamMeta(
                    key="anchor_units",
                    label="Anchor Units",
                    tooltip=(
                        "Position size (units) for each side of the hedged pair at the "
                        "anchor level. Usually smaller than the grid units."
                    ),
                    default=10.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="grid_units",
                    label="Grid Units",
                    tooltip=(
                        "Position size (units) for each side of the hedged pair at every "
                        "level other than the anchor."
                    ),
                    default=20.0,
                    min_value=1.0,
                    max_value=100.0,
                ),
                ParamMeta(
                    key="stop_buffer",
                    label="Stop Buffer ($)",
                    tooltip=(
                        "Extra distance past the next grid level where each stop-loss "
                        "sits. The stop is placed (gap-to-next-level + this buffer) from "
                        "the entry, so it triggers just after price reaches the adjacent "
                        "level against the position."
                    ),
                    default=1.0,
                    min_value=0.0,
                    max_value=10.0,
                ),
                ParamMeta(
                    key="max_levels_ahead",
                    label="Max Levels Ahead",
                    tooltip=(
                        "Maximum active levels kept on the side price is trending into. "
                        "As price advances and a further level opens, the trailing level "
                        "nearest the anchor is closed to free margin (banking its profit). "
                        "The anchor is never closed — its winning side rides the trend."
                    ),
                    default=2.0,
                    min_value=1.0,
                    max_value=10.0,
                ),
                ParamMeta(
                    key="max_levels_behind",
                    label="Max Levels Behind",
                    tooltip=(
                        "Maximum active levels kept on the trailing side (opposite the "
                        "direction price is moving). Lower values free more margin for the "
                        "trending side."
                    ),
                    default=1.0,
                    min_value=1.0,
                    max_value=10.0,
                ),
                ParamMeta(
                    key="session_profit_target",
                    label="Session Profit Target ($)",
                    tooltip=(
                        "When total session P&L reaches this target, close all positions "
                        "and restart fresh at the current price."
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
                        "close all positions and restart fresh."
                    ),
                    default=50.0,
                    min_value=10.0,
                    max_value=10000.0,
                ),
                ParamMeta(
                    key="daily_loss_limit",
                    label="Daily Loss Limit ($)",
                    tooltip=(
                        "When cumulative P&L across all sessions for the day drops below "
                        "this negative threshold, stop trading entirely. Resumes the next "
                        "day."
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
        """Generate signals: drain queue, check P&L exits, place/grow the grid."""
        if not bars:
            return None

        current_bar = bars[-1]
        self._current_price = current_bar.close
        current_date = current_bar.timestamp.strftime("%Y-%m-%d")

        # Day boundary reset
        if self._current_date and current_date != self._current_date:
            self._daily_realized_pnl = 0.0
            self._session_active = True
            self._session_count = 1
            self._session_history = []
            self._restart_session()
        self._current_date = current_date

        # Drain any queued signals first (one per cycle)
        if self._signal_queue:
            return self._signal_queue.popleft()

        if self._close_all_pending:
            self._close_all_pending = False
            return self._flat_close_all(current_bar, self._close_reason)

        # Re-emit FLAT while close-all is being retried by the engine
        if self._close_all_in_progress:
            return self._flat_close_all(current_bar, self._close_reason)

        if not self._session_active:
            return None

        # Session P&L exits (realized + unrealized)
        if self._anchor_price is not None:
            total_session_pnl = self._session_realized_pnl + self._session_unrealized_pnl
            if total_session_pnl >= self._session_profit_target:
                self._trigger_close_all("session_profit_target")
                return self._flat_close_all(current_bar, "session_profit_target")
            if total_session_pnl <= -self._session_loss_limit:
                self._trigger_close_all("session_loss_limit")
                return self._flat_close_all(current_bar, "session_loss_limit")

        # Initialise session: set anchor, build ladder, place the anchor pair
        # (at market — it sits at the current price) and the first levels out.
        if self._anchor_price is None:
            self._symbol = current_bar.symbol
            self._anchor_price = current_bar.close
            self._levels = self._build_ladder(current_bar.close)
            self._place_anchor_pair()
            self._maintain_grid()
            if self._signal_queue:
                return self._signal_queue.popleft()
            return None

        # Trim active levels beyond the caps (frees margin), then keep the resting
        # window staged ahead of price and re-fill any revisited stopped-out side.
        self._trim_active_levels()
        self._maintain_grid()
        if self._signal_queue:
            return self._signal_queue.popleft()

        return None

    def get_levels_to_close(self) -> list[str]:
        """Poll hook: grid keys the engine should close at market and stop tracking.

        Returned once each — the engine closes the trades (and cancels any pending
        orders) for these keys; their realized P&L flows back via report_trade_closed.
        """
        if not self._pending_close:
            return []
        keys = sorted(self._pending_close)
        self._pending_close.clear()
        return keys

    def _build_ladder(self, anchor: float) -> list[float]:
        """Compute the ladder of level prices around the anchor.

        The anchor itself is a level. The first level above/below is anchor_gap
        away; every level beyond that is grid_spacing apart.
        """
        levels: list[float] = [round(anchor, 2)]
        # Above: anchor + anchor_gap, then + grid_spacing each step
        first_above = anchor + self._anchor_gap
        for i in range(100):
            levels.append(round(first_above + i * self._grid_spacing, 2))
        # Below: anchor - anchor_gap, then - grid_spacing each step
        first_below = anchor - self._anchor_gap
        for i in range(100):
            levels.append(round(first_below - i * self._grid_spacing, 2))
        return sorted(set(levels))

    def _next_level(self, level: float, *, above: bool) -> float | None:
        """Return the adjacent ladder level above or below the given level."""
        if level not in self._levels:
            return None
        idx = self._levels.index(level)
        if above:
            return self._levels[idx + 1] if idx + 1 < len(self._levels) else None
        return self._levels[idx - 1] if idx - 1 >= 0 else None

    def _units_for(self, level: float) -> float:
        """Anchor level uses anchor_units; every other level uses grid_units."""
        if self._anchor_price is not None and level == round(self._anchor_price, 2):
            return self._anchor_units
        return self._grid_units

    def _order_type_for(self, level: float, side: str) -> str:
        """The resting order type chosen so the order sits at its exact price
        without filling early — above the market a buy is a STOP and a sell a
        LIMIT; below it a buy is a LIMIT and a sell a STOP.

        The anchor is NOT special-cased to MARKET here. Its initial pair is placed
        at market explicitly (``_place_anchor_pair`` passes ``market=True``); this
        path only runs when a side is (re-)placed by ``_maintain_grid``. If the
        anchor's losing leg has stopped out and price has since moved past it,
        re-entering at market would carry the original stop on the wrong side of
        the new entry — OANDA rejects it (``STOP_LOSS_ON_FILL_LOSS``). Re-arming it
        as a resting STOP/LIMIT at the anchor price instead only fills when price
        returns, where the stop is valid again.
        """
        above_price = self._side_entry(level, side) > self._current_price
        if side == "long":
            return "STOP" if above_price else "LIMIT"
        return "LIMIT" if above_price else "STOP"

    def _side_entry(self, level: float, side: str) -> float:
        """Resting entry price for a side: sell at the level, buy offset above it."""
        if side == "long":
            return round(level + self._buy_sell_offset, 2)
        return round(level, 2)

    def _side_stop_loss(self, level: float, side: str) -> float | None:
        """Stop just past the adjacent level in the side's losing direction.

        Distance from entry = (gap to that level) + stop_buffer. A buy is stopped
        below; a sell above.
        """
        if side == "long":
            next_below = self._next_level(level, above=False)
            if next_below is None:
                return None
            gap = level - next_below
            return round(self._side_entry(level, side) - (gap + self._stop_buffer), 2)
        next_above = self._next_level(level, above=True)
        if next_above is None:
            return None
        gap = next_above - level
        return round(self._side_entry(level, side) + (gap + self._stop_buffer), 2)

    def _place_anchor_pair(self) -> None:
        """Place the anchor's hedged pair as market orders (it sits at price)."""
        if self._anchor_price is None:
            return
        level = round(self._anchor_price, 2)
        for side in ("long", "short"):
            self._queue_side(level, side, market=True)

    def _direction(self) -> int:
        """+1 if price is at/above the anchor (up-trend), -1 if below (down-trend)."""
        if self._anchor_price is None:
            return 1
        return 1 if self._current_price >= self._anchor_price else -1

    def _maintain_grid(self) -> None:
        """Slide the resting-order window with price: retract behind, place ahead.

        Placement follows price directionally: the side price is moving into keeps
        ``max_levels_ahead`` unfilled levels resting, the trailing side keeps
        ``max_levels_behind``. As price moves the window follows, so an order is
        always waiting where price is headed. Resting (unfilled) levels that fall
        outside the window are cancelled so the footprint does not grow without
        bound; they re-place if price brings them back in. Retired levels (trimmed
        for margin) are never re-placed.
        """
        if self._anchor_price is None:
            return

        price = self._current_price
        direction = self._direction()
        ahead = self._max_levels_ahead
        behind = self._max_levels_behind
        n_above = ahead if direction > 0 else behind
        n_below = behind if direction > 0 else ahead

        above = [lv for lv in self._levels if lv > price][:n_above]
        below = [lv for lv in reversed(self._levels) if lv < price][:n_below]
        window = set(above) | set(below)

        self._retract_stale_placed(window)

        for level in sorted(window):
            if level in self._retired:
                continue
            for side in ("long", "short"):
                if side in self._filled.get(level, set()):
                    continue
                if side in self._placed.get(level, set()):
                    continue
                self._queue_side(level, side)

    def _retract_stale_placed(self, window: set[float]) -> None:
        """Cancel purely-resting levels that have fallen outside the window.

        Only levels with no filled side are retracted — a level holding an active
        trade is governed by the margin trim instead. The anchor is never
        retracted. Retracted levels are NOT marked retired: if price returns and
        the level re-enters the window, it is placed again (the window slides
        rather than leaving a permanent gap).
        """
        anchor = round(self._anchor_price, 2) if self._anchor_price is not None else None
        for level in list(self._placed):
            if level in window or level == anchor:
                continue
            if self._filled.get(level):
                continue  # has an active side — leave it to _trim_active_levels
            sides = self._placed.get(level, set())
            if not sides:
                continue
            for side in list(sides):
                self._pending_close.add(f"{level:.2f}_{side}")
            self._placed.pop(level, None)
            self._fill_prices.pop(level, None)

    def _trim_active_levels(self) -> None:
        """Retire active levels beyond the per-side caps to free margin.

        Caps are directional: the trending side keeps ``max_levels_ahead`` active
        levels, the trailing side ``max_levels_behind``. On each side the levels
        NEAREST the current price are kept and the rest are retired (closed). The
        anchor is exempt — it is never trimmed, so its winning side rides the move.
        """
        if self._anchor_price is None:
            return

        anchor = round(self._anchor_price, 2)
        direction = self._direction()
        cap_above = self._max_levels_ahead if direction > 0 else self._max_levels_behind
        cap_below = self._max_levels_behind if direction > 0 else self._max_levels_ahead

        active = [lv for lv, sides in self._filled.items() if sides]
        # Nearest current price first; keep the cap nearest, retire the rest.
        def _dist(lv: float) -> float:
            return abs(lv - self._current_price)

        above = sorted((lv for lv in active if lv > anchor), key=_dist)
        below = sorted((lv for lv in active if lv < anchor), key=_dist)

        for level in above[cap_above:]:
            self._retire_level(level)
        for level in below[cap_below:]:
            self._retire_level(level)

    def _retire_level(self, level: float) -> None:
        """Mark a level for closing (deliberate trim) and stop maintaining it."""
        if level in self._retired:
            return
        self._retired.add(level)
        # Hand every open side of this level to the engine to close at market.
        for side in self._filled.get(level, set()):
            self._pending_close.add(f"{level:.2f}_{side}")
        # Cancel any still-resting orders at this level so they don't re-fill.
        for side in self._placed.get(level, set()):
            self._pending_close.add(f"{level:.2f}_{side}")

    def _queue_side(self, level: float, side: str, *, market: bool = False) -> None:
        """Queue one resting (or market) entry for a given level and side.

        Order type is chosen so the order rests at its exact price without filling
        early: above the market a buy uses a STOP and a sell a LIMIT; below the
        market a buy uses a LIMIT and a sell a STOP. Orders too close to price are
        skipped this cycle and retried once price moves away.
        """
        entry = self._side_entry(level, side)
        stop_loss = self._side_stop_loss(level, side)
        if stop_loss is None:
            return  # Edge of ladder — no adjacent level to anchor the stop

        signal_type = SignalType.LONG if side == "long" else SignalType.SHORT

        if market:
            order_type = "MARKET"
            limit_price: float | None = None
        else:
            distance = entry - self._current_price
            if abs(distance) < self._MIN_RESTING_DISTANCE:
                return  # Too close — would fill immediately or be rejected
            order_type = self._order_type_for(level, side)
            limit_price = entry

        self._placed.setdefault(level, set()).add(side)
        self._fill_prices.setdefault(level, {})

        grid_key = f"{level:.2f}_{side}"
        self._order_types[grid_key] = order_type  # remember how it was entered
        units = self._units_for(level)

        metadata: dict[str, str] = {
            "grid_level": grid_key,
            "fixed_units": f"{units:.1f}",
            "order_type": order_type,
            "entry_price": f"{entry:.5f}",
        }
        if limit_price is not None:
            metadata["limit_price"] = f"{limit_price:.5f}"

        self._signal_queue.append(
            Signal(
                symbol=self._symbol,
                signal_type=signal_type,
                strategy_name=self.name,
                strength=1.0,
                metadata=metadata,
                stop_loss=stop_loss,
                take_profit=None,
            )
        )

    # --- Engine callbacks ---

    def report_fill(self, grid_level_key: str, fill_price: float) -> None:
        """Called by engine when a resting/market order fills.

        Moves the side from placed → filled and records the entry price.
        """
        parsed = self._parse_key(grid_level_key)
        if parsed is None:
            return
        level, side = parsed

        self._fill_prices.setdefault(level, {})[side] = fill_price
        self._placed.get(level, set()).discard(side)
        self._filled.setdefault(level, set()).add(side)
        self._stopped.get(level, set()).discard(side)

    def report_trade_closed(
        self, grid_level_key: str, realized_pnl: float, close_side: str = ""
    ) -> None:
        """Called by engine when a broker-side closure (stop-loss) is detected."""
        self._session_realized_pnl += realized_pnl
        self._daily_realized_pnl += realized_pnl

        if self._daily_realized_pnl <= -self._daily_loss_limit:
            self._close_reason = "daily_loss_limit"
            self._close_all_pending = True
            self._session_active = False

        parsed = self._parse_key(grid_level_key)
        if parsed is None:
            return
        level, side = parsed

        self._filled.get(level, set()).discard(side)
        self._fill_prices.get(level, {}).pop(side, None)

        # A retired level was closed deliberately to free margin — do NOT mark it
        # stopped (which would let a revisit re-open it). Leave it retired.
        if level in self._retired:
            self._placed.get(level, set()).discard(side)
            return

        # Otherwise this was a stop-loss hit — mark stopped so a revisit re-places
        # the missing side to complete the pair again.
        self._stopped.setdefault(level, set()).add(side)

    def on_signal_rejected(self, grid_level_key: str) -> None:
        """Called by engine when risk rejects or placement fails for a signal."""
        parsed = self._parse_key(grid_level_key)
        if parsed is None:
            return
        level, side = parsed

        # Drop any queued signal for this exact side so it can be re-placed later.
        self._signal_queue = deque(
            s for s in self._signal_queue
            if s.metadata.get("grid_level") != grid_level_key
        )
        self._placed.get(level, set()).discard(side)

    def release_level(self, grid_level: float) -> bool:
        """Compatibility fallback — no-op for this strategy."""
        return False

    def _parse_key(self, grid_level_key: str) -> tuple[float, str] | None:
        """Parse a "<price>_<side>" grid key into (level, side)."""
        parts = grid_level_key.rsplit("_", 1)
        if len(parts) != 2:
            return None
        level_str, side = parts
        if side not in ("long", "short"):
            return None
        try:
            return float(level_str), side
        except ValueError:
            return None

    # --- Close-all orchestration ---

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
        self._close_all_in_progress = True
        self._signal_queue.clear()
        self._session_history.append({
            "session": self._session_count,
            "reason": reason,
            "pnl": round(self._session_realized_pnl, 2),
        })

    def notify_close_all_complete(self) -> None:
        """Called by engine after all positions are closed. Restarts the session."""
        self._close_all_in_progress = False
        if self._session_active:
            self._restart_session()

    def _restart_session(self) -> None:
        """Reset session state for a fresh start. Preserves daily P&L and history."""
        self._anchor_price = None
        self._levels = []
        self._signal_queue = deque()
        self._placed = {}
        self._filled = {}
        self._stopped = {}
        self._fill_prices = {}
        self._order_types = {}
        self._pending_close = set()
        self._retired = set()
        self._session_realized_pnl = 0.0
        self._session_unrealized_pnl = 0.0
        self._close_all_pending = False
        self._close_all_in_progress = False
        self._session_count += 1

    # --- Display ---

    def get_display_state(self) -> dict[str, object] | None:
        """Return strategy-specific state for UI display (paired_grid shape)."""
        if self._anchor_price is None:
            return None

        anchor = round(self._anchor_price, 2)
        active = (
            set(self._placed) | set(self._filled) | set(self._stopped)
        )
        all_display = sorted(set(self._levels) | active)

        # Window around the active range (or the anchor if nothing is active yet)
        active_indices = [i for i, lv in enumerate(all_display) if lv in active]
        if active_indices:
            start = max(0, min(active_indices) - 3)
            end = min(len(all_display) - 1, max(active_indices) + 3)
        else:
            mid = all_display.index(anchor) if anchor in all_display else len(all_display) // 2
            start = max(0, mid - 3)
            end = min(len(all_display) - 1, mid + 3)

        grid_levels: list[dict[str, object]] = []
        for i in range(start, end + 1):
            level = all_display[i]
            grid_levels.append(self._level_display(level))
        grid_levels.reverse()  # Highest price first

        return {
            "type": "paired_grid",
            "anchor_price": anchor,
            "current_price": self._current_price,
            "grid_levels": grid_levels,
            "session_pnl": self._session_realized_pnl + self._session_unrealized_pnl,
            "session_realized_pnl": self._session_realized_pnl,
            "session_unrealized_pnl": self._session_unrealized_pnl,
            "session_profit_target": self._session_profit_target,
            "session_loss_limit": self._session_loss_limit,
            "daily_pnl": self._daily_realized_pnl + self._session_unrealized_pnl,
            "daily_loss_limit": self._daily_loss_limit,
            "session_count": self._session_count,
            "session_active": self._session_active,
            "filled_count": sum(len(s) for s in self._filled.values()),
            "placed_count": sum(len(s) for s in self._placed.values()),
            "session_history": list(self._session_history),
        }

    def _level_display(self, level: float) -> dict[str, object]:
        """Build the per-level display dict for one ladder level."""
        placed = self._placed.get(level, set())
        filled = self._filled.get(level, set())
        stopped = self._stopped.get(level, set())
        fills = self._fill_prices.get(level, {})

        def side_state(side: str) -> dict[str, object]:
            if side in filled:
                status = "active"
            elif side in placed:
                status = "placed"
            elif side in stopped:
                status = "stopped"
            else:
                status = "none"
            sl = self._side_stop_loss(level, side)
            # Prefer the type the side was actually entered as (stable once
            # placed/filled); fall back to the computed type for waiting levels.
            grid_key = f"{level:.2f}_{side}"
            order_type = self._order_types.get(grid_key) or self._order_type_for(level, side)
            return {
                "status": status,
                "fill": fills.get(side, 0.0),
                "sl": sl if sl is not None else 0.0,
                "units": self._units_for(level),
                "order_type": order_type.lower(),
            }

        if filled:
            status = "active"
        elif placed:
            status = "placed"
        elif stopped:
            status = "stopped"
        else:
            status = "waiting"

        return {
            "price": level,
            "status": status,
            "buy": side_state("long"),
            "sell": side_state("short"),
        }
