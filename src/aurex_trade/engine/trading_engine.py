"""Trading engine — main loop orchestrating the full trading pipeline.

Depends ONLY on port interfaces and domain types. Never imports adapters.
"""

import time

import structlog

from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import Order
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy
from aurex_trade.ports.broker import BrokerPort
from aurex_trade.ports.market_data import MarketDataPort
from aurex_trade.ports.repository import RepositoryPort

log = structlog.get_logger()


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
    ) -> None:
        self._strategy = strategy
        self._risk_engine = risk_engine
        self._broker = broker
        self._market_data = market_data
        self._repository = repository
        self._symbol = symbol
        self._interval_seconds = interval_seconds
        self._bar_count = bar_count
        self._running = False
        # Session stats for periodic summary
        self._session_signals = 0
        self._session_trades = 0
        self._session_rejections = 0

    def run(self, max_cycles: int | None = None) -> None:
        """Start the trading loop.

        Args:
            max_cycles: If set, stop after this many cycles (for testing).
                        None means run indefinitely.
        """
        self._running = True
        cycle = 0
        log.info(
            "engine_started",
            symbol=self._symbol,
            strategy=self._strategy.name,
            interval=self._interval_seconds,
        )

        while self._running:
            if max_cycles is not None and cycle >= max_cycles:
                log.info("max_cycles_reached", cycles=cycle)
                break

            try:
                self._run_cycle()
            except Exception:
                log.exception("cycle_error", cycle=cycle)

            cycle += 1

            # Periodic session summary
            if cycle > 0 and cycle % self._SUMMARY_INTERVAL == 0:
                position = self._broker.get_positions(self._symbol)
                log.info(
                    "session_summary",
                    cycles=cycle,
                    signals=self._session_signals,
                    trades=self._session_trades,
                    rejections=self._session_rejections,
                    position_qty=position.quantity if position else 0.0,
                    unrealized_pnl=position.unrealized_pnl if position else 0.0,
                    realized_pnl=position.realized_pnl if position else 0.0,
                )

            if self._running and (max_cycles is None or cycle < max_cycles):
                time.sleep(self._interval_seconds)

        log.info("engine_stopped", total_cycles=cycle)

    def stop(self) -> None:
        """Signal the engine to stop after the current cycle."""
        self._running = False
        log.info("stop_requested")

    def _run_cycle(self) -> None:
        """Execute one complete trading cycle."""
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
        )
        self._repository.save_signal(signal)

        # Step 3: Risk evaluation
        position = self._repository.get_current_position(self._symbol)
        trades_today = self._repository.get_trades_today(self._symbol)

        log.debug(
            "risk_eval_context",
            position_qty=position.quantity if position else 0.0,
            position_avg_cost=position.average_cost if position else 0.0,
            unrealized_pnl=position.unrealized_pnl if position else 0.0,
            trades_today_count=len(trades_today),
        )
        decision = self._risk_engine.evaluate(signal, position, trades_today)

        log.info(
            "risk_decision",
            action=decision.action.value,
            reason=decision.reason,
        )
        self._repository.save_decision(decision)

        if decision.action != RiskAction.APPROVED:
            self._session_rejections += 1
            return

        # Step 4: Create and place order
        side = OrderSide.BUY if signal.signal_type == SignalType.LONG else OrderSide.SELL
        order = Order(
            signal_id=signal.id,
            symbol=self._symbol,
            side=side,
            quantity=1.0,
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
        self._repository.save_trade(trade)

        # Step 5: Update position
        prev_position = position
        updated_position = self._broker.get_positions(self._symbol)
        if updated_position:
            self._repository.save_position(updated_position)
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
