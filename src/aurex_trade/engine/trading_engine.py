"""Trading engine — main loop orchestrating the full trading pipeline.

Depends ONLY on port interfaces and domain types. Never imports adapters.
"""

import time
from datetime import UTC, datetime
from typing import TypedDict

import structlog

from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import AccountState, Order
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy
from aurex_trade.ports.broker import BrokerPort
from aurex_trade.ports.market_data import MarketDataPort
from aurex_trade.ports.repository import RepositoryPort

log = structlog.get_logger()


class EquitySnapshot(TypedDict):
    """A single equity reading at a point in time."""

    timestamp: str
    equity: float


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


class TradingEngine:
    """Orchestrates: fetch data -> strategy -> risk -> execute -> persist.

    The engine checks the kill switch at the top of every cycle AND before
    every order placement. Errors skip the current cycle — never crash.
    """

    # Log a session summary every this many cycles
    _SUMMARY_INTERVAL: int = 60

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
        self._running = False
        # Session stats for periodic summary
        self._session_signals = 0
        self._session_trades = 0
        self._session_rejections = 0
        # Account state tracking for risk engine
        self._peak_equity: float = 0.0
        self._trade_pnls: list[float] = []
        # Observability (read by web layer via get_metrics())
        self._cycle_count: int = 0
        self._started_at: datetime | None = None
        # Equity history for live chart
        self._equity_history: list[EquitySnapshot] = []

    def run(self, max_cycles: int | None = None) -> None:
        """Start the trading loop.

        Args:
            max_cycles: If set, stop after this many cycles (for testing).
                        None means run indefinitely.
        """
        self._running = True
        self._cycle_count = 0
        self._started_at = datetime.now(UTC)

        # Initialize peak equity
        self._peak_equity = self._broker.equity

        log.info(
            "engine_started",
            symbol=self._symbol,
            strategy=self._strategy.name,
            interval=self._interval_seconds,
            initial_equity=self._peak_equity,
        )

        while self._running:
            if max_cycles is not None and self._cycle_count >= max_cycles:
                log.info("max_cycles_reached", cycles=self._cycle_count)
                break

            try:
                self._run_cycle()
            except Exception:
                log.exception("cycle_error", cycle=self._cycle_count)

            self._cycle_count += 1

            # Periodic session summary
            if self._cycle_count > 0 and self._cycle_count % self._SUMMARY_INTERVAL == 0:
                position = self._broker.get_positions(self._symbol)
                log.info(
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
                time.sleep(self._interval_seconds)

        self._running = False
        self._started_at = None
        log.info("engine_stopped", total_cycles=self._cycle_count)

    def stop(self) -> None:
        """Signal the engine to stop after the current cycle."""
        self._running = False
        log.info("stop_requested")

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
        )

    def _run_cycle(self) -> None:
        """Execute one complete trading cycle."""
        # Record equity snapshot for live chart
        current_eq = self._broker.equity
        self._equity_history.append(
            EquitySnapshot(
                timestamp=datetime.now(UTC).isoformat(),
                equity=current_eq,
            )
        )

        # Step 1: Fetch market data
        bars = self._market_data.get_latest_bars(self._symbol, self._bar_count)

        if not bars:
            log.warning("no_bars_returned", symbol=self._symbol)
            return

        latest_close = bars[-1].close
        log.info(
            "bars_fetched",
            symbol=self._symbol,
            count=len(bars),
            latest_close=latest_close,
            latest_time=bars[-1].timestamp.isoformat(),
        )

        # Step 2: Generate signal
        signal = self._strategy.generate(bars)

        if signal is None:
            log.debug("no_signal", strategy=self._strategy.name)
            return

        self._session_signals += 1
        log.info(
            "signal_generated",
            signal_type=signal.signal_type.value,
            strength=signal.strength,
            strategy=signal.strategy_name,
            trigger_price=latest_close,
            stop_loss=signal.stop_loss,
        )
        self._repository.save_signal(signal, user_id=self._user_id)

        # Step 3: Risk evaluation
        position = self._repository.get_current_position(
            self._symbol, user_id=self._user_id
        )
        trades_today = self._repository.get_trades_today(
            self._symbol, user_id=self._user_id
        )

        # Assemble account state for risk engine
        current_equity = self._broker.equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        account_state = AccountState(
            equity=current_equity, peak_equity=self._peak_equity
        )

        log.debug(
            "risk_eval_context",
            position_qty=position.quantity if position else 0.0,
            position_avg_cost=position.average_cost if position else 0.0,
            unrealized_pnl=position.unrealized_pnl if position else 0.0,
            trades_today_count=len(trades_today),
            equity=current_equity,
            peak_equity=self._peak_equity,
        )
        decision = self._risk_engine.evaluate(
            signal,
            position,
            trades_today,
            account_state=account_state,
            recent_trade_pnls=self._trade_pnls,
        )

        log.info(
            "risk_decision",
            action=decision.action.value,
            reason=decision.reason,
        )
        self._repository.save_decision(decision, user_id=self._user_id)

        if decision.action != RiskAction.APPROVED:
            self._session_rejections += 1
            return

        # Step 4: Calculate position size and create order
        side = OrderSide.BUY if signal.signal_type == SignalType.LONG else OrderSide.SELL

        quantity = self._risk_engine.calculate_position_size(
            signal, account_state, latest_close
        )
        if quantity <= 0.0:
            quantity = min(
                self._fallback_position_size,
                float(self._risk_engine._max_position_size),
            )

        order = Order(
            signal_id=signal.id,
            symbol=self._symbol,
            side=side,
            quantity=quantity,
            stop_loss=signal.stop_loss,
        )

        trade = self._broker.place_order(order)
        self._session_trades += 1
        slippage = trade.price - latest_close
        log.info(
            "trade_executed",
            side=trade.side.value,
            quantity=trade.quantity,
            price=trade.price,
            trigger_price=latest_close,
            slippage=round(slippage, 4),
        )
        self._repository.save_trade(trade, user_id=self._user_id)

        # Step 5: Update position and track P&L
        prev_position = position
        updated_position = self._broker.get_positions(self._symbol)

        # Track trade P&L for consecutive loss detection
        prev_realized = prev_position.realized_pnl if prev_position else 0.0
        new_realized = updated_position.realized_pnl if updated_position else 0.0
        trade_pnl = new_realized - prev_realized
        if trade_pnl != 0.0:
            self._trade_pnls.append(trade_pnl)

        # Update peak equity
        current_equity = self._broker.equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        if updated_position:
            self._repository.save_position(updated_position, user_id=self._user_id)
            log.info(
                "position_updated",
                quantity=updated_position.quantity,
                avg_cost=updated_position.average_cost,
                unrealized_pnl=updated_position.unrealized_pnl,
                realized_pnl=updated_position.realized_pnl,
            )
        elif prev_position and prev_position.quantity != 0.0:
            # Position was closed — log round-trip result
            log.info(
                "position_closed",
                symbol=self._symbol,
                realized_pnl=prev_position.realized_pnl,
                entry_price=prev_position.average_cost,
                exit_price=trade.price,
                quantity=prev_position.quantity,
            )
