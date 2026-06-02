"""Trading engine — main loop orchestrating the full trading pipeline.

Depends ONLY on port interfaces and domain types. Never imports adapters.
"""

import contextlib
import time
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import structlog

from aurex_trade.domain.enums import OrderSide, OrderType, RiskAction, SignalType
from aurex_trade.domain.models import (
    AccountState,
    OpenBrokerTrade,
    Order,
    Position,
    Signal,
    Trade,
)
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy
from aurex_trade.ports.broker import BrokerPort
from aurex_trade.ports.market_data import MarketDataPort
from aurex_trade.ports.repository import RepositoryPort

log = structlog.get_logger()


class EquitySnapshot(TypedDict):
    """A single equity + price reading at a point in time."""

    timestamp: str
    equity: float
    price: float


class TradeMarker(TypedDict):
    """A trade event for chart overlay."""

    timestamp: str
    price: float
    side: str
    quantity: float
    stop_loss: float | None
    take_profit: float | None
    broker_trade_id: str


class EventLogEntry(TypedDict):
    """A timestamped event for the UI event log."""

    timestamp: str
    event: str
    details: str


class EngineMetrics(TypedDict):
    """Snapshot of engine state, safe to read from any thread (GIL-protected)."""

    cycle_count: int
    started_at: datetime | None
    running: bool
    session_signals: int
    session_trades: int
    session_rejections: int
    current_equity: float
    balance: float
    unrealized_pnl: float
    open_position_count: int
    peak_equity: float
    uptime_seconds: float | None
    open_units: float
    open_side: str
    realized_pnl: float
    win_rate: float | None
    avg_slippage: float | None
    current_price: float | None


class TradingEngine:
    """Orchestrates: fetch data -> strategy -> risk -> execute -> persist.

    The engine checks the kill switch at the top of every cycle AND before
    every order placement. Errors skip the current cycle — never crash.
    """

    # Log a session summary every this many strategy cycles
    _SUMMARY_INTERVAL: int = 60
    # Fast poll interval (seconds) for fill/closure detection
    _FILL_POLL_INTERVAL: int = 5

    def __init__(
        self,
        strategy: Strategy,
        risk_engine: RiskEngine,
        broker: BrokerPort,
        market_data: MarketDataPort,
        repository: RepositoryPort,
        symbol: str,
        interval_seconds: int,
        bar_count: int = 50,
        fallback_position_size: float = 1.0,
        *,
        user_id: str,
        strategy_params: dict[str, int | float] | None = None,
        risk_params: dict[str, int | float | bool] | None = None,
    ) -> None:
        self._strategy = strategy
        self._risk_engine = risk_engine
        self._broker = broker
        self._market_data = market_data
        self._repository = repository
        self._symbol = symbol
        self._interval_seconds = interval_seconds
        self._bar_count = bar_count
        self._fallback_position_size = fallback_position_size
        self._user_id = user_id
        self._strategy_params = strategy_params or {}
        self._risk_params = risk_params or {}
        self._log = log.bind(user_id=user_id)
        self._running = False
        # Session stats for periodic summary
        self._session_signals = 0
        self._session_trades = 0
        self._session_rejections = 0
        # Account state tracking for risk engine
        self._peak_equity: float = 0.0
        self._trade_pnls: list[float] = []
        self._slippages: list[float] = []
        # Observability (read by web layer via get_metrics())
        self._cycle_count: int = 0
        self._started_at: datetime | None = None
        self._last_price: float | None = None
        # Equity history for live chart
        self._equity_history: list[EquitySnapshot] = []
        # Trade markers for chart overlay
        self._trade_markers: list[TradeMarker] = []
        # Grid level key → broker trade ID mapping for closure detection
        self._grid_trade_map: dict[str, str] = {}
        self._grid_logged: bool = False
        # Pending limit orders: grid_level_key → broker_order_id
        self._pending_order_map: dict[str, str] = {}
        # Metadata for pending orders (for placing opposite side on fill)
        self._pending_order_meta: dict[str, dict[str, str]] = {}
        # Event log for UI
        self._event_log: list[EventLogEntry] = []
        # Close-all circuit breaker state
        self._close_all_failed_count: int = 0
        self._close_all_next_retry_at: datetime | None = None
        # Hard safety cap on open trades (0 = disabled)
        self._max_open_trades: int = 20

    def run(self, max_cycles: int | None = None) -> None:
        """Start the trading loop.

        Uses two intervals:
        - Fast poll (5s): fill detection + closure detection
        - Strategy cycle (interval_seconds): candles, signal generation, order placement

        Args:
            max_cycles: If set, stop after this many strategy cycles (for testing).
                        None means run indefinitely.
        """
        self._running = True
        self._cycle_count = 0
        self._started_at = datetime.now(UTC)

        # Initialize peak equity
        self._peak_equity = self._broker.equity

        self._log.info(
            "engine_started",
            symbol=self._symbol,
            strategy=self._strategy.name,
            interval=self._interval_seconds,
            fill_poll_interval=self._FILL_POLL_INTERVAL,
            initial_equity=self._peak_equity,
            strategy_params=self._strategy_params,
            risk_params=self._risk_params,
        )

        last_strategy_time = 0.0

        while self._running:
            if max_cycles is not None and self._cycle_count >= max_cycles:
                self._log.info("max_cycles_reached", cycles=self._cycle_count)
                break

            now = time.monotonic()

            # Fast poll: fill detection + closure detection every cycle
            try:
                self._run_fast_poll()
            except Exception:
                self._log.exception("fast_poll_error")

            # Strategy cycle: run at configured interval
            elapsed = now - last_strategy_time
            if elapsed >= self._interval_seconds or last_strategy_time == 0.0:
                last_strategy_time = now
                try:
                    self._run_strategy_cycle()
                except Exception:
                    self._log.exception("cycle_error", cycle=self._cycle_count)

                self._cycle_count += 1

                # Periodic session summary
                if (
                    self._cycle_count > 0
                    and self._cycle_count % self._SUMMARY_INTERVAL == 0
                ):
                    position = self._broker.get_positions(self._symbol)
                    self._log.info(
                        "session_summary",
                        cycles=self._cycle_count,
                        signals=self._session_signals,
                        trades=self._session_trades,
                        rejections=self._session_rejections,
                        equity=self._broker.equity,
                        peak_equity=self._peak_equity,
                        position_qty=position.quantity if position else 0.0,
                        unrealized_pnl=position.unrealized_pnl if position else 0.0,
                        realized_pnl=position.realized_pnl if position else 0.0,
                    )

            if self._running and (max_cycles is None or self._cycle_count < max_cycles):
                sleep_seconds = self._FILL_POLL_INTERVAL if self._interval_seconds > 0 else 0
                if sleep_seconds:
                    time.sleep(sleep_seconds)

        self._running = False
        self._started_at = None
        self._log.info("engine_stopped", total_cycles=self._cycle_count)

    def stop(self) -> None:
        """Signal the engine to stop after the current cycle."""
        self._running = False
        # Cancel any pending limit orders at the broker
        if self._pending_order_map:
            try:
                cancelled = self._broker.cancel_all_orders(self._symbol)
                if cancelled:
                    self._log.info("pending_orders_cancelled_on_stop", count=cancelled)
            except Exception:
                self._log.exception("cancel_pending_on_stop_failed")
            self._pending_order_map.clear()
            self._pending_order_meta.clear()
        self._log.info("stop_requested")

    @property
    def kill_switch(self) -> bool:
        """Whether the kill switch is currently active."""
        return self._risk_engine.kill_switch

    @kill_switch.setter
    def kill_switch(self, value: bool) -> None:
        self._risk_engine.kill_switch = value

    def get_equity_history(self) -> list[EquitySnapshot]:
        """Return the equity history for charting. Thread-safe (GIL)."""
        return list(self._equity_history)

    def get_trade_markers(self) -> list[TradeMarker]:
        """Return trade markers for chart overlay. Thread-safe (GIL)."""
        return list(self._trade_markers)

    def get_event_log(self) -> list[EventLogEntry]:
        """Return event log for UI display. Thread-safe (GIL)."""
        return list(self._event_log)

    def get_strategy_state(self) -> dict[str, object] | None:
        """Return strategy-specific display state, if available.

        Enriches strategy state with engine-level broker IDs and quantities.
        """
        get_state = getattr(self._strategy, "get_display_state", None)
        if get_state is None:
            return None
        result: dict[str, object] | None = get_state()
        if result is None:
            return None

        # Enrich grid levels with broker ticket IDs and units
        grid_levels = result.get("grid_levels")
        if isinstance(grid_levels, list):
            grid_units = getattr(self._strategy, "_grid_units", None)
            for level_info in grid_levels:
                if not isinstance(level_info, dict):
                    continue
                price = level_info.get("price")
                if price is None:
                    continue
                level_str = f"{float(price):.2f}"
                for side_key, grid_suffix in (("buy", "long"), ("sell", "short")):
                    side_info = level_info.get(side_key)
                    if not isinstance(side_info, dict):
                        continue
                    grid_key = f"{level_str}_{grid_suffix}"
                    # Add broker trade ID (filled trades)
                    trade_id = self._grid_trade_map.get(grid_key)
                    if trade_id:
                        side_info["ticket"] = trade_id
                    # Add broker order ID (pending limits)
                    order_id = self._pending_order_map.get(grid_key)
                    if order_id:
                        side_info["order_id"] = order_id
                    # Add units
                    if grid_units is not None:
                        side_info["units"] = float(grid_units)

        return result

    def get_risk_summary(self) -> dict[str, float]:
        """Return risk summary calculations for UI display.

        Adapts to strategy type:
        - Hedged grid: uses fixed units, session loss limit as effective cap
        - Other strategies: uses dynamic position sizing from risk engine
        """
        equity = self._broker.equity
        stop_distance = 30.0

        # Extract stop_distance from strategy if available
        strategy_stop = getattr(self._strategy, "_stop_distance", None)
        if strategy_stop is not None:
            stop_distance = float(strategy_stop)

        max_position_size = float(self._risk_engine._max_position_size)

        # Check if strategy uses fixed units (hedged grid pattern)
        grid_units = getattr(self._strategy, "_grid_units", None)
        session_loss_limit = getattr(self._strategy, "_session_loss_limit", None)
        daily_loss_limit_attr = getattr(self._strategy, "_daily_loss_limit", None)

        if grid_units is not None:
            # Hedged grid: fixed units, paired trades
            units_per_trade = float(grid_units)

            # Worst case per pair: both sides stop out (whipsaw)
            # = 2 * units * stop_distance
            # Max concurrent pairs before session limit triggers close-all
            loss_per_pair_worst = 2.0 * units_per_trade * stop_distance
            if loss_per_pair_worst > 0 and session_loss_limit is not None:
                max_pairs = max(1, int(float(session_loss_limit) / loss_per_pair_worst) + 1)
            else:
                max_pairs = 5

            worst_case_loss = max_pairs * loss_per_pair_worst

            # Effective loss cap is the tighter of session and daily limits
            if daily_loss_limit_attr is not None:
                effective_limit = float(daily_loss_limit_attr)
            elif session_loss_limit is not None:
                effective_limit = float(session_loss_limit)
            else:
                effective_limit = equity * self._risk_engine._max_drawdown_pct

            headroom = effective_limit - abs(
                getattr(self._strategy, "_daily_realized_pnl", 0.0)
            )

            return {
                "units_per_trade": round(units_per_trade, 1),
                "max_position_size": max_position_size,
                "worst_case_loss": round(worst_case_loss, 2),
                "drawdown_limit": round(effective_limit, 2),
                "headroom": round(headroom, 2),
                "stop_distance": stop_distance,
                "risk_enabled": self._risk_engine._enabled,
            }

        # Default: dynamic position sizing
        risk_per_trade = self._risk_engine._risk_per_trade
        max_drawdown_pct = self._risk_engine._max_drawdown_pct

        raw_units = (equity * risk_per_trade) / stop_distance if stop_distance > 0 else 0
        units_per_trade = min(raw_units, max_position_size)

        max_trades = 10
        strategy_max_levels = getattr(self._strategy, "_max_levels", None)
        if strategy_max_levels is not None:
            max_trades = int(strategy_max_levels)

        worst_case_loss = max_trades * units_per_trade * stop_distance
        drawdown_limit = equity * max_drawdown_pct
        headroom = drawdown_limit - worst_case_loss

        return {
            "units_per_trade": round(units_per_trade, 1),
            "max_position_size": max_position_size,
            "worst_case_loss": round(worst_case_loss, 2),
            "drawdown_limit": round(drawdown_limit, 2),
            "headroom": round(headroom, 2),
            "stop_distance": stop_distance,
            "risk_enabled": self._risk_engine._enabled,
        }

    def get_metrics(self) -> EngineMetrics:
        """Return a snapshot of current engine metrics.

        Safe to call from any thread — all accessed attributes are simple
        types protected by the GIL for atomic reads.
        """
        uptime: float | None = None
        started = self._started_at
        if started is not None:
            uptime = (datetime.now(UTC) - started).total_seconds()

        # Get detailed account info if broker supports it
        balance = 0.0
        unrealized_pnl = 0.0
        open_position_count = 0
        current_equity = self._broker.equity

        if hasattr(self._broker, "get_account_summary"):
            summary = self._broker.get_account_summary()
            balance = summary["balance"]
            unrealized_pnl = summary["unrealized_pnl"]
            open_position_count = int(summary["open_position_count"])
        else:
            balance = current_equity

        # Open units from broker position
        position = self._broker.get_positions(self._symbol)
        open_units = position.quantity if position else 0.0
        if open_units > 0:
            open_side = "long"
        elif open_units < 0:
            open_side = "short"
        else:
            open_side = "flat"

        # Realized P&L from broker
        realized_pnl = position.realized_pnl if position else 0.0

        # Win rate from session trade P&Ls
        win_rate: float | None = None
        if self._trade_pnls:
            wins = sum(1 for p in self._trade_pnls if p > 0)
            win_rate = wins / len(self._trade_pnls)

        # Average slippage
        avg_slippage: float | None = None
        if self._slippages:
            avg_slippage = sum(self._slippages) / len(self._slippages)

        return EngineMetrics(
            cycle_count=self._cycle_count,
            started_at=self._started_at,
            running=self._running,
            session_signals=self._session_signals,
            session_trades=self._session_trades,
            session_rejections=self._session_rejections,
            current_equity=current_equity,
            balance=balance,
            unrealized_pnl=unrealized_pnl,
            open_position_count=open_position_count,
            peak_equity=self._peak_equity,
            uptime_seconds=uptime,
            open_units=abs(open_units),
            open_side=open_side,
            realized_pnl=realized_pnl,
            win_rate=win_rate,
            avg_slippage=avg_slippage,
            current_price=self._last_price,
        )

    def _check_limit_fills(self, open_trades: list[OpenBrokerTrade]) -> None:
        """Detect limit orders that have been filled or cancelled.

        Compares broker pending orders against local map. Missing orders are
        verified against open trades — if a matching new trade exists, it was
        filled. Otherwise, it was cancelled/expired and we release the level.
        """
        if not self._pending_order_map:
            return

        try:
            broker_pending = self._broker.get_pending_orders(self._symbol)
        except Exception:
            self._log.exception("check_limit_fills_error")
            return

        broker_pending_ids = {o.broker_order_id for o in broker_pending}

        # Find orders that disappeared from broker's pending list
        disappeared: list[tuple[str, str]] = []
        for grid_key, broker_order_id in self._pending_order_map.items():
            if broker_order_id not in broker_pending_ids:
                disappeared.append((grid_key, broker_order_id))

        if not disappeared:
            return

        # Use provided open trades to identify fills via diff against grid_trade_map
        known_trade_ids = set(self._grid_trade_map.values())
        # New trades = open trades not already tracked
        new_trades = [t for t in open_trades if t.broker_trade_id not in known_trade_ids]

        for grid_key, broker_order_id in disappeared:
            del self._pending_order_map[grid_key]

            # Match by side + price proximity (handles multiple same-side fills)
            expected_side = OrderSide.BUY if grid_key.endswith("_long") else OrderSide.SELL
            # Extract expected price from grid_key (e.g. "4570.00_long" → 4570.0)
            level_str = grid_key.rsplit("_", 1)[0]
            try:
                expected_price = float(level_str)
            except ValueError:
                expected_price = 0.0

            broker_trade_id = ""
            fill_price = 0.0
            best_idx = -1
            best_distance = float("inf")

            for i, t in enumerate(new_trades):
                if t.side == expected_side:
                    distance = abs(t.open_price - expected_price)
                    if distance < best_distance:
                        best_distance = distance
                        best_idx = i

            if best_idx >= 0:
                matched = new_trades.pop(best_idx)
                broker_trade_id = matched.broker_trade_id
                fill_price = matched.open_price

            if not broker_trade_id:
                # No matching trade found — order was likely cancelled, not filled
                self._log.warning(
                    "limit_order_cancelled_or_expired",
                    grid_level=grid_key,
                    broker_order_id=broker_order_id,
                )
                # Release level back to strategy
                on_rejected = getattr(self._strategy, "on_signal_rejected", None)
                if on_rejected is not None:
                    on_rejected(grid_key)
                continue

            # Confirmed fill — map for closure detection
            self._grid_trade_map[grid_key] = broker_trade_id

            # Report fill to strategy
            report_fill = getattr(self._strategy, "report_fill", None)
            if report_fill is not None:
                report_fill(grid_key, fill_price)

            side_label = "BUY" if grid_key.endswith("_long") else "SELL"

            self._log.info(
                "limit_order_filled",
                grid_level=grid_key,
                broker_order_id=broker_order_id,
                broker_trade_id=broker_trade_id,
                fill_price=fill_price,
                side=side_label,
            )
            self._event_log.append(EventLogEntry(
                timestamp=datetime.now(UTC).isoformat(),
                event="limit_fill",
                details=(
                    f"{side_label} limit filled @ {fill_price:.2f}"
                    f" (#{broker_trade_id})"
                ),
            ))

            # Record marker for chart overlay
            self._trade_markers.append(
                TradeMarker(
                    timestamp=datetime.now(UTC).isoformat(),
                    price=fill_price,
                    side=side_label.lower(),
                    quantity=0.0,
                    stop_loss=None,
                    take_profit=None,
                    broker_trade_id=broker_trade_id,
                )
            )

            self._session_trades += 1

            # Place opposite-side market order immediately
            self._place_opposite_market_order(grid_key, fill_price)

    def _place_opposite_market_order(
        self, grid_key: str, limit_fill_price: float
    ) -> None:
        """Place the opposite-side market order after a limit fill.

        Reads metadata stored when the limit was placed to determine the
        opposite side, grid key, units, and stop loss.
        """
        meta = self._pending_order_meta.pop(grid_key, None)
        if not meta:
            self._log.warning(
                "no_metadata_for_opposite_order",
                grid_key=grid_key,
            )
            return

        opposite_side_str = meta.get("opposite_side", "")
        opposite_grid_key = meta.get("opposite_grid_level", "")
        opposite_stop_str = meta.get("opposite_stop_loss", "")
        fixed_units_str = meta.get("fixed_units", "1.0")

        if not opposite_side_str or not opposite_grid_key:
            return

        side = OrderSide.BUY if opposite_side_str == "BUY" else OrderSide.SELL
        quantity = float(fixed_units_str)
        if self._risk_engine._enabled:
            quantity = min(quantity, float(self._risk_engine._max_position_size))
        stop_loss = float(opposite_stop_str) if opposite_stop_str else None

        order = Order(
            symbol=self._symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            stop_loss=stop_loss,
        )

        try:
            trade = self._broker.place_order(order)
        except Exception:
            self._log.exception(
                "opposite_market_order_failed",
                grid_key=opposite_grid_key,
                side=opposite_side_str,
            )
            return

        broker_trade_id = trade.broker_trade_id

        # Track for closure detection
        if broker_trade_id:
            self._grid_trade_map[opposite_grid_key] = broker_trade_id

        # Report fill to strategy
        report_fill = getattr(self._strategy, "report_fill", None)
        if report_fill is not None:
            report_fill(opposite_grid_key, trade.price)

        # Calculate slippage vs the grid level price
        level_str = opposite_grid_key.rsplit("_", 1)[0]
        try:
            level_price = float(level_str)
        except ValueError:
            level_price = limit_fill_price
        slippage = abs(trade.price - level_price)
        self._slippages.append(slippage)

        self._log.info(
            "opposite_market_filled",
            grid_level=opposite_grid_key,
            side=opposite_side_str,
            fill_price=trade.price,
            slippage=round(slippage, 4),
            broker_trade_id=broker_trade_id,
        )
        self._event_log.append(EventLogEntry(
            timestamp=datetime.now(UTC).isoformat(),
            event="market_fill",
            details=(
                f"{opposite_side_str} market @ {trade.price:.2f}"
                f" (#{broker_trade_id}, slippage ${slippage:.2f})"
            ),
        ))

        # Record marker for chart overlay
        self._trade_markers.append(
            TradeMarker(
                timestamp=datetime.now(UTC).isoformat(),
                price=trade.price,
                side=opposite_side_str.lower(),
                quantity=quantity,
                stop_loss=stop_loss,
                take_profit=None,
                broker_trade_id=broker_trade_id,
            )
        )

        self._session_trades += 1

    # Circuit breaker for close-all: exponential backoff + max retries
    _MAX_CLOSE_ALL_RETRIES: int = 5
    _CLOSE_ALL_BASE_BACKOFF: int = 10  # seconds

    def _close_all_trades(self, reason: str) -> None:
        """Close all open trades for the symbol and clear grid state.

        Used by strategies with session P&L exits (e.g., paired grid) to
        liquidate all positions when a profit target or loss limit is hit.

        Only notifies strategy (triggering session restart) if ALL trades are
        successfully closed. On partial failure, increments retry counter and
        schedules backoff. After max retries, stops the engine.
        """
        # Circuit breaker: check if we've exceeded max retries
        if self._close_all_failed_count >= self._MAX_CLOSE_ALL_RETRIES:
            self._log.error(
                "close_all_circuit_breaker",
                retries=self._close_all_failed_count,
                reason=reason,
            )
            self._running = False
            return

        # Backoff: skip if waiting for next retry window
        if self._close_all_next_retry_at is not None:
            if datetime.now(UTC) < self._close_all_next_retry_at:
                return
            # Backoff expired — proceed with retry
            self._close_all_next_retry_at = None

        # Cancel all pending limit orders first
        try:
            cancelled = self._broker.cancel_all_orders(self._symbol)
            if cancelled:
                self._log.info("pending_orders_cancelled", count=cancelled, reason=reason)
        except Exception:
            self._log.exception("cancel_pending_orders_failed")

        open_trades = self._broker.get_open_trades(self._symbol)
        if not open_trades:
            self._log.info("close_all_no_trades", reason=reason)
            # Success — no trades to close
            self._close_all_failed_count = 0
            self._close_all_next_retry_at = None
            self._pending_order_map.clear()
            self._pending_order_meta.clear()
            self._grid_trade_map.clear()
            self._event_log.append(EventLogEntry(
                timestamp=datetime.now(UTC).isoformat(),
                event="close_all",
                details=f"All positions closed: {reason}",
            ))
            notify = getattr(self._strategy, "notify_close_all_complete", None)
            if notify is not None:
                notify()
            return

        # Close each trade using dedicated close endpoint
        failed = 0
        for trade in open_trades:
            try:
                self._broker.close_trade(trade.broker_trade_id)
            except Exception:
                failed += 1
                if failed == 1:
                    self._log.exception(
                        "close_all_order_failed",
                        broker_trade_id=trade.broker_trade_id,
                    )

        if failed > 0:
            # Log summary of failures (avoid spamming 86 identical messages)
            if failed > 1:
                self._log.error(
                    "close_all_order_failed_summary",
                    failed_count=failed,
                    total=len(open_trades),
                )
            self._close_all_failed_count += 1
            backoff = self._CLOSE_ALL_BASE_BACKOFF * (
                2 ** (self._close_all_failed_count - 1)
            )
            self._close_all_next_retry_at = datetime.now(UTC) + timedelta(
                seconds=backoff
            )
            self._log.warning(
                "close_all_retry_scheduled",
                attempt=self._close_all_failed_count,
                max_retries=self._MAX_CLOSE_ALL_RETRIES,
                next_retry_in_seconds=backoff,
                reason=reason,
            )
            return  # Do NOT notify strategy — trades still open

        # Verify broker confirms zero remaining trades (defense in depth)
        remaining = self._broker.get_open_trades(self._symbol)
        if remaining:
            self._log.error(
                "close_all_residual_trades",
                count=len(remaining),
                reason=reason,
            )
            self._close_all_failed_count += 1
            return  # Treat as failure

        # All trades closed successfully
        self._close_all_failed_count = 0
        self._close_all_next_retry_at = None
        self._pending_order_map.clear()
        self._pending_order_meta.clear()
        self._grid_trade_map.clear()

        self._log.info(
            "close_all_executed",
            reason=reason,
            trades_closed=len(open_trades),
        )

        self._event_log.append(EventLogEntry(
            timestamp=datetime.now(UTC).isoformat(),
            event="close_all",
            details=f"All positions closed: {reason}",
        ))

        # Notify strategy that close-all is complete (for session restart)
        notify = getattr(self._strategy, "notify_close_all_complete", None)
        if notify is not None:
            notify()

    def _check_closures(
        self,
        open_trades: list[OpenBrokerTrade],
        skip_trade_ids: set[str] | None = None,
    ) -> None:
        """Detect trades closed by the broker (TP/SL hit) and release grid levels."""
        if not self._grid_trade_map:
            return

        open_trade_ids = {t.broker_trade_id for t in open_trades}
        skip = skip_trade_ids or set()

        keys_to_free: list[tuple[str, str]] = []
        for grid_key, broker_id in self._grid_trade_map.items():
            if broker_id in skip:
                continue
            if broker_id not in open_trade_ids:
                keys_to_free.append((grid_key, broker_id))

        for grid_key, broker_id in keys_to_free:
            # Query close details from broker (best-effort — don't block release)
            side = "close_sl"
            close_price = 0.0
            realized_pnl = 0.0
            try:
                details = self._broker.get_closed_trade_details(broker_id)
                if details:
                    close_reason = details.close_reason
                    side = "close_tp" if "TAKE_PROFIT" in close_reason else "close_sl"
                    close_price = details.close_price
                    realized_pnl = details.realized_pnl
            except Exception:
                self._log.warning(
                    "closed_trade_details_unavailable",
                    broker_trade_id=broker_id,
                )

            # Remove from map (always runs)
            del self._grid_trade_map[grid_key]

            # Track P&L for consecutive loss detection in risk engine
            if realized_pnl != 0.0:
                self._trade_pnls.append(realized_pnl)

            # Report trade closure to strategy (for P&L tracking)
            report_closed = getattr(self._strategy, "report_trade_closed", None)
            if report_closed is not None:
                report_closed(grid_key, realized_pnl)

            # Release the grid level back to 'waiting' (legacy float-key strategies)
            release = getattr(self._strategy, "release_level", None)
            if release is not None:
                with contextlib.suppress(ValueError):
                    release(float(grid_key))

            # Record close marker for chart
            self._trade_markers.append(
                TradeMarker(
                    timestamp=datetime.now(UTC).isoformat(),
                    price=close_price,
                    side=side,
                    quantity=0.0,
                    stop_loss=None,
                    take_profit=None,
                    broker_trade_id=broker_id,
                )
            )

            close_label = "TP" if "tp" in side else "SL"
            pnl_str = (
                f"+${realized_pnl:.2f}" if realized_pnl >= 0
                else f"-${abs(realized_pnl):.2f}"
            )
            self._event_log.append(EventLogEntry(
                timestamp=datetime.now(UTC).isoformat(),
                event=side,
                details=f"{close_label} @ {close_price:.2f} (#{broker_id}) P&L: {pnl_str}",
            ))

            self._log.info(
                "trade_closed_by_broker",
                grid_level=grid_key,
                broker_trade_id=broker_id,
                close_reason=side,
                close_price=close_price,
                realized_pnl=realized_pnl,
            )

    def _run_fast_poll(self) -> None:
        """Fast poll: detect fills and closures (runs every 5s)."""
        open_trades = self._broker.get_open_trades(self._symbol)
        # Snapshot trade IDs BEFORE fill detection — any new IDs added during
        # _check_limit_fills (opposite market orders) must be skipped by closure
        # detection since they weren't in the open_trades fetch.
        trade_ids_before = set(self._grid_trade_map.values())
        self._check_limit_fills(open_trades)
        newly_added = set(self._grid_trade_map.values()) - trade_ids_before
        self._check_closures(open_trades, skip_trade_ids=newly_added)

    def _run_strategy_cycle(self) -> None:
        """Strategy cycle: fetch data, generate signals, place orders."""
        # Step 1: Fetch market data
        bars = self._market_data.get_latest_bars(self._symbol, self._bar_count)

        if not bars:
            self._log.warning("no_bars_returned", symbol=self._symbol)
            return

        latest_close = bars[-1].close
        self._last_price = latest_close

        # Record equity + price snapshot for live charts
        self._equity_history.append(
            EquitySnapshot(
                timestamp=datetime.now(UTC).isoformat(),
                equity=self._broker.equity,
                price=latest_close,
            )
        )
        self._log.info(
            "bars_fetched",
            symbol=self._symbol,
            count=len(bars),
            latest_close=latest_close,
            latest_time=bars[-1].timestamp.isoformat(),
        )

        # Feed unrealized P&L to strategy (for session P&L tracking)
        update_pnl = getattr(self._strategy, "update_unrealized_pnl", None)
        if update_pnl is not None:
            if hasattr(self._broker, "get_account_summary"):
                summary = self._broker.get_account_summary()
                update_pnl(summary["unrealized_pnl"])
            else:
                update_pnl(0.0)

        # Step 2: Generate signals — drain all queued signals in one cycle
        all_signals: list[Signal] = []
        signal = self._strategy.generate(bars)

        # Log grid levels once after strategy initializes
        if not self._grid_logged:
            state = self.get_strategy_state()
            if state and "anchor_price" in state:
                self._log.info(
                    "grid_initialized",
                    anchor_price=state["anchor_price"],
                    levels=state.get("levels") or state.get("grid_levels"),
                )
                self._grid_logged = True

        if signal is None:
            self._log.debug("no_signal", strategy=self._strategy.name)
            return

        # Collect all signals (strategy may queue multiple, capped for safety)
        all_signals.append(signal)
        max_drain = 50
        for _ in range(max_drain):
            next_signal = self._strategy.generate(bars)
            if next_signal is None:
                break
            all_signals.append(next_signal)
        else:
            self._log.warning("signal_drain_limit_reached", max=max_drain)

        # Fetch position once for all signals (doesn't change during limit placement)
        position = self._broker.get_positions(self._symbol)
        trades_today = self._repository.get_trades_today(
            self._symbol, user_id=self._user_id
        )
        # Cache open trade count for max-trades cap (avoid repeated API calls)
        open_trade_count = len(self._broker.get_open_trades(self._symbol))

        # Process each signal
        for sig in all_signals:
            self._process_signal(sig, latest_close, position, trades_today, open_trade_count)

    def _process_signal(
        self,
        sig: Signal,
        latest_close: float,
        pos: Position | None,
        trades_list: list[Trade],
        open_trade_count: int = 0,
    ) -> None:
        """Process a single signal: risk check → order placement.

        Handles both MARKET and LIMIT order types.
        """

        # Handle close-all signals (bypass risk engine — this IS risk management)
        if (
            sig.signal_type == SignalType.FLAT
            and sig.metadata.get("action") == "close_all"
        ):
            reason = sig.metadata.get("reason", "unknown")
            self._close_all_trades(reason)
            return

        # Hard safety cap: reject new orders if too many trades open
        if self._max_open_trades > 0 and open_trade_count >= self._max_open_trades:
            self._log.warning(
                "max_open_trades_reached",
                current=open_trade_count,
                max=self._max_open_trades,
            )
            return

        self._session_signals += 1
        self._log.info(
            "signal_generated",
            signal_type=sig.signal_type.value,
            strength=sig.strength,
            strategy=sig.strategy_name,
            trigger_price=latest_close,
            stop_loss=sig.stop_loss,
            order_type=sig.metadata.get("order_type", "MARKET"),
        )
        self._repository.save_signal(sig, user_id=self._user_id)

        # Assemble account state for risk engine
        current_equity = self._broker.equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        account_state = AccountState(equity=current_equity, peak_equity=self._peak_equity)

        self._log.debug(
            "risk_eval_context",
            position_qty=pos.quantity if pos else 0.0,
            position_avg_cost=pos.average_cost if pos else 0.0,
            unrealized_pnl=pos.unrealized_pnl if pos else 0.0,
            trades_today_count=len(trades_list),
            equity=current_equity,
            peak_equity=self._peak_equity,
        )
        decision = self._risk_engine.evaluate(
            sig,
            pos,
            trades_list,
            account_state=account_state,
            recent_trade_pnls=self._trade_pnls,
        )

        self._log.info(
            "risk_decision",
            action=decision.action.value,
            reason=decision.reason,
        )
        self._repository.save_decision(decision, user_id=self._user_id)

        if decision.action != RiskAction.APPROVED:
            self._session_rejections += 1
            self._event_log.append(EventLogEntry(
                timestamp=datetime.now(UTC).isoformat(),
                event="rejected",
                details=f"{sig.signal_type.value.upper()} rejected: {decision.reason}",
            ))
            # Release grid level so it can re-trigger once conditions improve.
            grid_level_str = sig.metadata.get("grid_level")
            if grid_level_str:
                on_rejected = getattr(self._strategy, "on_signal_rejected", None)
                if on_rejected is not None:
                    on_rejected(grid_level_str)
                release = getattr(self._strategy, "release_level", None)
                if release is not None:
                    with contextlib.suppress(ValueError):
                        release(float(grid_level_str))
            return

        # Step 4: Calculate position size and create order
        side = OrderSide.BUY if sig.signal_type == SignalType.LONG else OrderSide.SELL

        # Prefer strategy-specified fixed units
        fixed_units_str = sig.metadata.get("fixed_units")
        if fixed_units_str:
            quantity = float(fixed_units_str)
            if self._risk_engine._enabled:
                quantity = min(quantity, float(self._risk_engine._max_position_size))
        else:
            quantity = self._risk_engine.calculate_position_size(
                sig, account_state, latest_close
            )
            if quantity <= 0.0:
                quantity = self._fallback_position_size
            if self._risk_engine._enabled:
                quantity = min(quantity, float(self._risk_engine._max_position_size))

        # Determine order type from signal metadata
        is_limit = sig.metadata.get("order_type") == "LIMIT"
        limit_price_str = sig.metadata.get("limit_price")
        limit_price = float(limit_price_str) if limit_price_str else None

        order = Order(
            signal_id=sig.id,
            symbol=self._symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.LIMIT if is_limit else OrderType.MARKET,
            limit_price=limit_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
        )

        if is_limit:
            self._place_limit_order(order, sig)
        else:
            self._place_market_order(order, sig, latest_close)

    def _place_limit_order(self, order: Order, signal: Signal) -> None:
        """Place a limit order and track it in the pending map."""
        try:
            trade = self._broker.place_order(order)
        except Exception:
            self._log.exception(
                "limit_order_placement_failed",
                symbol=self._symbol,
                side=order.side.value,
                limit_price=order.limit_price,
            )
            grid_level_str = signal.metadata.get("grid_level")
            if grid_level_str:
                on_rejected = getattr(self._strategy, "on_signal_rejected", None)
                if on_rejected is not None:
                    on_rejected(grid_level_str)
            return

        # Track pending order for fill detection
        grid_level_str = signal.metadata.get("grid_level")
        broker_order_id = trade.broker_trade_id
        if grid_level_str and broker_order_id:
            self._pending_order_map[grid_level_str] = broker_order_id
            # Store metadata for placing opposite side on fill
            self._pending_order_meta[grid_level_str] = dict(signal.metadata)

        self._log.info(
            "limit_order_placed",
            side=order.side.value,
            quantity=order.quantity,
            limit_price=order.limit_price,
            broker_order_id=broker_order_id,
            grid_level=grid_level_str,
        )
        self._event_log.append(EventLogEntry(
            timestamp=datetime.now(UTC).isoformat(),
            event="limit_placed",
            details=(
                f"{order.side.value.upper()} {order.quantity}"
                f" limit @ {order.limit_price:.2f} (#{broker_order_id})"
            ),
        ))

    def _place_market_order(self, order: Order, signal: Signal, latest_close: float) -> None:
        """Place a market order (original flow)."""
        try:
            trade = self._broker.place_order(order)
        except Exception:
            self._log.exception(
                "order_execution_failed", symbol=self._symbol, side=order.side.value
            )
            grid_level_str = signal.metadata.get("grid_level")
            if grid_level_str:
                release = getattr(self._strategy, "release_level", None)
                if release is not None:
                    release(float(grid_level_str))
            return

        self._session_trades += 1
        # Slippage: compare fill price to intended entry price from signal metadata
        intended_price_str = signal.metadata.get("entry_price")
        intended_price = float(intended_price_str) if intended_price_str else latest_close
        slippage = trade.price - intended_price
        self._slippages.append(abs(slippage))

        broker_trade_id = trade.broker_trade_id

        self._log.info(
            "trade_executed",
            side=trade.side.value,
            quantity=trade.quantity,
            price=trade.price,
            trigger_price=latest_close,
            slippage=round(slippage, 4),
            broker_trade_id=broker_trade_id,
        )
        self._event_log.append(EventLogEntry(
            timestamp=datetime.now(UTC).isoformat(),
            event="trade",
            details=(
                f"{trade.side.value.upper()} {trade.quantity}"
                f" @ {trade.price:.2f} (#{broker_trade_id})"
            ),
        ))
        self._repository.save_trade(trade, user_id=self._user_id)

        # Record marker for chart overlay
        self._trade_markers.append(
            TradeMarker(
                timestamp=datetime.now(UTC).isoformat(),
                price=trade.price,
                side=trade.side.value,
                quantity=trade.quantity,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                broker_trade_id=broker_trade_id,
            )
        )

        # Track grid level key → broker trade ID for closure detection
        grid_level_str = signal.metadata.get("grid_level")
        if grid_level_str and broker_trade_id:
            self._grid_trade_map[grid_level_str] = broker_trade_id

        # Report actual fill price to strategy (updates grid entry/SL display)
        if grid_level_str:
            report_fill = getattr(self._strategy, "report_fill", None)
            if report_fill is not None:
                report_fill(grid_level_str, trade.price)

        # Step 5: Update position tracking
        updated_position = self._broker.get_positions(self._symbol)

        # Update peak equity
        current_equity = self._broker.equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        if updated_position:
            self._repository.save_position(updated_position, user_id=self._user_id)
            self._log.info(
                "position_updated",
                quantity=updated_position.quantity,
                avg_cost=updated_position.average_cost,
                unrealized_pnl=updated_position.unrealized_pnl,
                realized_pnl=updated_position.realized_pnl,
            )
