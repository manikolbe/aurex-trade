"""Backtest runner — orchestrates historical replay through strategy and risk.

Supports two modes:
- Simple: stateless strategies — single signal per bar, risk-gated
- Grid: stateful strategies (ciby_sliding_grid, ciby_hedged_doubling_grid) —
  signal drain, limit orders, stop-loss enforcement, strategy callbacks, risk
  engine disabled

Mode is auto-detected via `hasattr(strategy, "report_fill")`.
"""

from __future__ import annotations

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter, _OpenTrade
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.results import BacktestResult, BacktestTradeRecord
from aurex_trade.domain.enums import OrderSide, OrderType, RiskAction, SignalType
from aurex_trade.domain.models import AccountState, BarData, ClosedTradeInfo, Order, Signal
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy
from aurex_trade.metrics import calculate_metrics
from aurex_trade.ports.repository import RepositoryPort


class BacktestRunner:
    """Replays historical bars through a strategy, producing BacktestResult.

    For simple strategies: bars -> signal -> risk -> order -> position update.
    For grid strategies: bars -> process_bar -> callbacks -> drain signals -> place orders.
    """

    def __init__(
        self,
        strategy: Strategy,
        risk_engine: RiskEngine,
        market_data: HistoricalMarketDataAdapter,
        broker: SimulatedBrokerAdapter,
        repository: RepositoryPort,
        config: BacktestConfig,
        *,
        user_id: str,
    ) -> None:
        self._strategy = strategy
        self._risk_engine = risk_engine
        self._market_data = market_data
        self._broker = broker
        self._repository = repository
        self._config = config
        self._user_id = user_id
        self._peak_equity: float = config.initial_capital
        self._trade_pnls: list[float] = []

        # Grid mode state
        self._is_grid = hasattr(strategy, "report_fill")
        self._pending_order_map: dict[str, str] = {}  # grid_key → broker_order_id
        self._pending_order_meta: dict[str, dict[str, str]] = {}
        self._grid_trade_map: dict[str, str] = {}  # grid_key → broker_trade_id

    def run(self) -> BacktestResult:
        """Execute the full backtest and return results."""
        equity_curve: list[float] = [self._config.initial_capital]
        trade_records: list[BacktestTradeRecord] = []
        bar_index = 0

        while not self._market_data.is_exhausted:
            current_bar = self._market_data.current_bar
            self._broker.set_current_bar(current_bar)

            if self._is_grid:
                records = self._run_grid_step(bar_index)
                trade_records.extend(records)
            else:
                prev_realized = self._get_realized_pnl()
                record = self._run_simple_step(bar_index)
                if record is not None:
                    trade_records.append(record)
                    new_realized = self._get_realized_pnl()
                    pnl = new_realized - prev_realized
                    if pnl != 0.0:
                        self._trade_pnls.append(pnl)

            # Record equity after this step and update peak
            current_equity = self._broker.equity
            if current_equity > self._peak_equity:
                self._peak_equity = current_equity
            equity_curve.append(current_equity)
            bar_index += 1

            # Advance to next bar
            self._market_data.advance()

        # Calculate metrics
        metrics = calculate_metrics(
            equity_curve=equity_curve,
            trade_pnls=self._trade_pnls,
            initial_capital=self._config.initial_capital,
            total_commission=self._broker.total_commission,
        )

        # Determine date range from data
        bars = self._market_data.get_latest_bars(self._config.symbol, 1)
        start_date = bars[0].timestamp if bars else None

        return BacktestResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trades=trade_records,
            strategy_name=self._strategy.name,
            symbol=self._config.symbol,
            start_date=start_date,
            end_date=current_bar.timestamp if bar_index > 0 else None,
            parameters={},
        )

    # ──────────────────────────────────────────────────────────────────────
    # Grid mode
    # ──────────────────────────────────────────────────────────────────────

    def _run_grid_step(self, bar_index: int) -> list[BacktestTradeRecord]:
        """Execute one grid trading step: process bar, callbacks, drain signals."""
        records: list[BacktestTradeRecord] = []

        # 1. Process bar — trigger SLs and fill limits
        newly_filled, newly_closed = self._broker.process_bar(self._market_data.current_bar)

        # 2. Handle fills — call report_fill + place opposite market order
        self._handle_fills(newly_filled)

        # 3. Handle closures — call report_trade_closed
        self._handle_closures(newly_closed)

        # 4. Update unrealized P&L for strategy
        update_pnl = getattr(self._strategy, "update_unrealized_pnl", None)
        if update_pnl is not None:
            unrealized = self._calculate_grid_unrealized()
            update_pnl(unrealized)

        # 5. Drain signals from strategy
        bars = self._market_data.get_latest_bars(self._config.symbol, self._config.bar_count)
        if not bars:
            return records

        signal_count = 0
        signal = self._strategy.generate(bars)
        while signal is not None and signal_count < 50:
            signal_count += 1
            record = self._process_grid_signal(signal, bar_index, bars)
            if record is not None:
                records.append(record)
            signal = self._strategy.generate(bars)

        # 6. Trim levels the strategy retired for margin (close trades + cancel orders)
        self._handle_levels_to_close()

        return records

    def _handle_levels_to_close(self) -> None:
        """Close + cancel grid keys the strategy marked for trimming (margin mgmt)."""
        get_levels = getattr(self._strategy, "get_levels_to_close", None)
        if get_levels is None:
            return
        report_closed = getattr(self._strategy, "report_trade_closed", None)
        for grid_key in get_levels():
            # Cancel a still-resting order at this key, if any.
            order_id = self._pending_order_map.pop(grid_key, None)
            if order_id is not None:
                self._pending_order_meta.pop(grid_key, None)
                self._broker.cancel_pending_order(order_id)

            # Close an open trade at this key, banking realized P&L.
            trade_id = self._grid_trade_map.get(grid_key)
            if trade_id is None:
                continue
            self._broker.close_trade(trade_id)
            details = self._broker.get_closed_trade_details(trade_id)
            realized_pnl = details.realized_pnl if details else 0.0
            self._trade_pnls.append(realized_pnl)
            del self._grid_trade_map[grid_key]
            if report_closed is not None:
                report_closed(grid_key, realized_pnl, "trim")

    def _handle_fills(self, newly_filled: list[_OpenTrade]) -> None:
        """Process limit order fills: match to grid keys, report, place opposite."""
        report_fill = getattr(self._strategy, "report_fill", None)

        for trade in newly_filled:
            # The broker stamps the grid key onto the filled trade (copied from
            # the pending order), so use it directly — robust to the spread/slippage
            # now applied to resting fills. Fall back to price-matching only if a
            # fill arrives without a stamped key.
            grid_key = trade.grid_level_key or self._find_grid_key_by_pending_fill(trade)
            if grid_key:
                self._pending_order_map.pop(grid_key, None)

            if grid_key:
                # Update tracking: move from pending to active
                self._grid_trade_map[grid_key] = trade.broker_trade_id
                # Update the trade's grid_level_key on the broker side
                for bt in self._broker._open_trades:
                    if bt.broker_trade_id == trade.broker_trade_id:
                        bt.grid_level_key = grid_key
                        break

                if report_fill:
                    report_fill(grid_key, trade.open_price)

                # Place opposite market order
                self._place_opposite_market_order(grid_key, trade)

    def _handle_closures(self, newly_closed: list[ClosedTradeInfo]) -> None:
        """Process trade closures: report to strategy, track P&L."""
        report_closed = getattr(self._strategy, "report_trade_closed", None)

        for closed in newly_closed:
            # Find grid key by broker_trade_id
            grid_key = None
            for key, trade_id in list(self._grid_trade_map.items()):
                if trade_id == closed.broker_trade_id:
                    grid_key = key
                    break

            if grid_key:
                if report_closed:
                    report_closed(grid_key, closed.realized_pnl)
                self._trade_pnls.append(closed.realized_pnl)
                del self._grid_trade_map[grid_key]

    def _find_grid_key_by_pending_fill(self, trade: _OpenTrade) -> str | None:
        """Fallback: match a fill to a grid key when it carries no stamped key.

        The primary path uses ``trade.grid_level_key`` (stamped by the broker).
        This is only reached for an unstamped fill: match the pending order that
        just disappeared whose price is within one fill's worth of friction
        (half-spread + max slippage + gap) of the fill price.
        """
        tolerance = self._broker._spread / 2.0 + self._broker._slippage + 0.01
        for grid_key, broker_order_id in list(self._pending_order_map.items()):
            still_pending = any(
                p.broker_order_id == broker_order_id for p in self._broker._pending_orders
            )
            if not still_pending:
                meta = self._pending_order_meta.get(grid_key, {})
                expected_price = float(meta.get("limit_price", "0"))
                if abs(trade.open_price - expected_price) <= tolerance:
                    del self._pending_order_map[grid_key]
                    return grid_key
        return None

    def _place_opposite_market_order(self, grid_key: str, filled_trade: _OpenTrade) -> None:
        """After a limit fills, place the opposite side as a market order."""
        meta = self._pending_order_meta.get(grid_key, {})
        if not meta:
            return

        opposite_side_str = meta.get("opposite_side", "")
        opposite_grid_key = meta.get("opposite_grid_level", "")
        opposite_stop_loss_str = meta.get("opposite_stop_loss", "")
        fixed_units_str = meta.get("fixed_units", "")

        if not opposite_side_str or not opposite_grid_key:
            return

        opposite_side = OrderSide.BUY if opposite_side_str == "BUY" else OrderSide.SELL
        opposite_stop_loss = float(opposite_stop_loss_str) if opposite_stop_loss_str else None
        quantity = float(fixed_units_str) if fixed_units_str else filled_trade.quantity

        order = Order(
            symbol=filled_trade.symbol,
            side=opposite_side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            stop_loss=opposite_stop_loss,
        )
        trade = self._broker.place_order(order)

        # Track the opposite side
        self._grid_trade_map[opposite_grid_key] = trade.broker_trade_id
        # Update grid_level_key on the broker's open trade
        for bt in self._broker._open_trades:
            if bt.broker_trade_id == trade.broker_trade_id:
                bt.grid_level_key = opposite_grid_key
                break

        # Report fill to strategy
        report_fill = getattr(self._strategy, "report_fill", None)
        if report_fill:
            report_fill(opposite_grid_key, trade.price)

        # Clean up metadata
        self._pending_order_meta.pop(grid_key, None)

    def _process_grid_signal(
        self,
        signal: Signal,
        bar_index: int,
        bars: list[BarData],
    ) -> BacktestTradeRecord | None:
        """Process a single signal from a grid strategy."""
        # Handle FLAT / close_all
        if signal.signal_type == SignalType.FLAT and signal.metadata.get("action") == "close_all":
            self._close_all_trades(signal.metadata.get("reason", ""))
            return None

        # Determine order type from metadata. LIMIT and STOP are both resting
        # entry orders (pending until price reaches them); STOP carries its
        # trigger price in the limit_price field, same as LIMIT.
        order_type_str = signal.metadata.get("order_type", "MARKET")
        is_limit = order_type_str == "LIMIT"
        is_stop = order_type_str == "STOP"
        is_resting = is_limit or is_stop

        side = OrderSide.BUY if signal.signal_type == SignalType.LONG else OrderSide.SELL
        quantity = float(signal.metadata.get("fixed_units", self._config.position_size))
        grid_key = signal.metadata.get("grid_level", "")

        if is_resting:
            limit_price = float(signal.metadata.get("limit_price", "0"))
            order = Order(
                signal_id=signal.id,
                symbol=self._config.symbol,
                side=side,
                order_type=OrderType.STOP if is_stop else OrderType.LIMIT,
                quantity=quantity,
                limit_price=limit_price,
                stop_loss=signal.stop_loss,
            )
            trade = self._broker.place_order(order)

            # Track pending order
            if grid_key:
                self._pending_order_map[grid_key] = trade.broker_trade_id
                self._pending_order_meta[grid_key] = dict(signal.metadata)
                # Update grid_level_key on the broker's pending order
                for p in self._broker._pending_orders:
                    if p.broker_order_id == trade.broker_trade_id:
                        p.grid_level_key = grid_key
                        break

            return None  # No trade record for pending limits

        # Market order
        order = Order(
            signal_id=signal.id,
            symbol=self._config.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            stop_loss=signal.stop_loss,
        )
        trade = self._broker.place_order(order)

        # Track in grid map
        if grid_key:
            self._grid_trade_map[grid_key] = trade.broker_trade_id
            for bt in self._broker._open_trades:
                if bt.broker_trade_id == trade.broker_trade_id:
                    bt.grid_level_key = grid_key
                    break

            report_fill = getattr(self._strategy, "report_fill", None)
            if report_fill:
                report_fill(grid_key, trade.price)

        return BacktestTradeRecord(
            trade=trade,
            signal=signal,
            bar_index=bar_index,
            equity_after=self._broker.equity,
        )

    def _close_all_trades(self, reason: str) -> None:
        """Close all open trades and cancel all pending orders."""
        # Cancel pending orders
        self._broker.cancel_all_orders(self._config.symbol)
        self._pending_order_map.clear()
        self._pending_order_meta.clear()

        # Close all open trades at market
        for trade in list(self._broker._open_trades):
            if trade.symbol == self._config.symbol:
                self._broker.close_trade(trade.broker_trade_id)
                closed = self._broker.get_closed_trade_details(trade.broker_trade_id)
                if closed:
                    # Find grid key and report
                    for key, tid in list(self._grid_trade_map.items()):
                        if tid == trade.broker_trade_id:
                            report_closed = getattr(self._strategy, "report_trade_closed", None)
                            if report_closed:
                                report_closed(key, closed.realized_pnl)
                            self._trade_pnls.append(closed.realized_pnl)
                            del self._grid_trade_map[key]
                            break

        self._grid_trade_map.clear()

        # Notify strategy that close_all is complete
        notify = getattr(self._strategy, "notify_close_all_complete", None)
        if notify:
            notify()

    def _calculate_grid_unrealized(self) -> float:
        """Calculate total unrealized P&L across all open grid trades."""
        if self._broker._current_bar is None:
            return 0.0
        price = self._broker._current_bar.close
        unrealized = 0.0
        for trade in self._broker._open_trades:
            if trade.side == OrderSide.BUY:
                unrealized += trade.quantity * (price - trade.open_price)
            else:
                unrealized += trade.quantity * (trade.open_price - price)
        return round(unrealized, 2)

    # ──────────────────────────────────────────────────────────────────────
    # Simple mode (original behavior)
    # ──────────────────────────────────────────────────────────────────────

    def _run_simple_step(self, bar_index: int) -> BacktestTradeRecord | None:
        """Execute one trading step for simple strategies."""
        # Step 1: Get bars for strategy
        bars = self._market_data.get_latest_bars(self._config.symbol, self._config.bar_count)
        if not bars:
            return None

        # Step 2: Generate signal
        signal = self._strategy.generate(bars)
        if signal is None:
            return None

        self._repository.save_signal(signal, user_id=self._user_id)

        # Step 3: Risk evaluation with account state
        position = self._repository.get_current_position(
            self._config.symbol, user_id=self._user_id
        )
        trades_today = self._repository.get_trades_today(
            self._config.symbol, user_id=self._user_id
        )

        current_equity = self._broker.equity
        account_state = AccountState(equity=current_equity, peak_equity=self._peak_equity)

        decision = self._risk_engine.evaluate(
            signal,
            position,
            trades_today,
            account_state=account_state,
            recent_trade_pnls=self._trade_pnls,
        )
        self._repository.save_decision(decision, user_id=self._user_id)

        if decision.action != RiskAction.APPROVED:
            return None

        # Step 4: Calculate position size and place order
        side = OrderSide.BUY if signal.signal_type == SignalType.LONG else OrderSide.SELL
        entry_price = bars[-1].close

        quantity = self._risk_engine.calculate_position_size(signal, account_state, entry_price)
        if quantity <= 0.0:
            quantity = self._config.position_size

        # Cap at configured max
        quantity = min(quantity, self._config.position_size)

        order = Order(
            signal_id=signal.id,
            symbol=self._config.symbol,
            side=side,
            quantity=quantity,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
        )

        trade = self._broker.place_order(order)
        self._repository.save_trade(trade, user_id=self._user_id)

        # Step 5: Update position
        updated_position = self._broker.get_positions(self._config.symbol)
        if updated_position:
            self._repository.save_position(updated_position, user_id=self._user_id)

        return BacktestTradeRecord(
            trade=trade,
            signal=signal,
            bar_index=bar_index,
            equity_after=self._broker.equity,
        )

    def _get_realized_pnl(self) -> float:
        """Get the broker's current total realized P&L."""
        position = self._broker.get_positions(self._config.symbol)
        return position.realized_pnl if position else 0.0
