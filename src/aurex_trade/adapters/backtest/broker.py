"""Simulated broker adapter — fills orders with spread and slippage.

Tracks positions, capital, and P&L internally. Deterministic via seeded RNG.
Satisfies BrokerPort Protocol.

Supports two modes:
- Simple: single net position per symbol (SMA, RSI strategies)
- Grid: multiple individual open trades with pending limit orders and stop-loss
  enforcement (ciby_hedged_grid and similar stateful strategies)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID, uuid4

from aurex_trade.domain.enums import OrderSide, OrderType
from aurex_trade.domain.models import (
    BarData,
    ClosedTradeInfo,
    OpenBrokerTrade,
    Order,
    PendingOrder,
    Position,
    Trade,
)


@dataclass
class _PendingLimitOrder:
    """Internal tracking for a limit order awaiting fill."""

    broker_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    limit_price: float
    stop_loss: float | None
    grid_level_key: str
    metadata: dict[str, str]


@dataclass
class _OpenTrade:
    """Internal tracking for an individual open trade."""

    broker_trade_id: str
    symbol: str
    side: OrderSide
    quantity: float
    open_price: float
    stop_loss: float | None
    grid_level_key: str


class SimulatedBrokerAdapter:
    """Simulated broker for backtesting.

    Fills at current_bar.close ± half_spread ± random_slippage.
    Tracks positions with full P&L accounting. Supports both simple net-position
    mode and individual trade tracking for grid strategies.
    """

    def __init__(
        self,
        initial_capital: float,
        spread: float = 0.015,
        slippage: float = 0.005,
        commission_per_trade: float = 0.0,
        seed: int = 42,
        *,
        grid_mode: bool = False,
    ) -> None:
        self._capital = initial_capital
        self._initial_capital = initial_capital
        self._spread = spread
        self._slippage = slippage
        self._commission_per_trade = commission_per_trade
        self._rng = random.Random(seed)  # noqa: S311
        self._positions: dict[str, Position] = {}
        self._current_bar: BarData | None = None
        self._total_commission: float = 0.0
        self._grid_mode = grid_mode

        # Grid mode state
        self._pending_orders: list[_PendingLimitOrder] = []
        self._open_trades: list[_OpenTrade] = []
        self._closed_trades: dict[str, ClosedTradeInfo] = {}

    def set_current_bar(self, bar: BarData) -> None:
        """Update the current market bar (called by runner each step)."""
        self._current_bar = bar

    def place_order(self, order: Order) -> Trade:
        """Place an order. LIMIT orders become pending; MARKET orders fill immediately."""
        if self._current_bar is None:
            msg = "No current bar set — call set_current_bar() first"
            raise RuntimeError(msg)

        if order.order_type == OrderType.LIMIT:
            return self._place_limit_order(order)

        return self._fill_market_order(order)

    def _place_limit_order(self, order: Order) -> Trade:
        """Create a pending limit order (does not fill immediately)."""
        assert self._current_bar is not None
        broker_order_id = str(uuid4())

        grid_level_key = ""
        metadata: dict[str, str] = {}
        if hasattr(order, "signal_id"):
            # Metadata will be attached by the runner via _pending_order_meta
            pass

        self._pending_orders.append(
            _PendingLimitOrder(
                broker_order_id=broker_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                limit_price=order.limit_price or 0.0,
                stop_loss=order.stop_loss,
                grid_level_key=grid_level_key,
                metadata=metadata,
            )
        )

        # Return a Trade with the broker_order_id for tracking.
        # Price is limit_price (placement, not fill).
        return Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.limit_price or 0.0,
            commission=0.0,
            broker_trade_id=broker_order_id,
            timestamp=self._current_bar.timestamp,
        )

    def _fill_market_order(self, order: Order) -> Trade:
        """Fill a market order immediately with spread + slippage."""
        assert self._current_bar is not None
        fill_price = self._calculate_fill_price(order.side)
        commission = self._commission_per_trade
        self._total_commission += commission
        self._capital -= commission

        if not self._grid_mode:
            self._update_position(order.symbol, order.side, order.quantity, fill_price)

        broker_trade_id = str(uuid4())

        # Track as open trade for grid mode
        self._open_trades.append(
            _OpenTrade(
                broker_trade_id=broker_trade_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                open_price=fill_price,
                stop_loss=order.stop_loss,
                grid_level_key="",  # Set by runner after placement
            )
        )

        return Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=commission,
            broker_trade_id=broker_trade_id,
            timestamp=self._current_bar.timestamp,
        )

    def process_bar(self, bar: BarData) -> tuple[list[_OpenTrade], list[ClosedTradeInfo]]:
        """Process a bar: trigger stop-losses and fill limit orders.

        Called each step BEFORE strategy generates signals.
        Returns (newly_filled_trades, newly_closed_trades) for the runner to
        drive strategy callbacks.

        Order of operations (conservative):
        1. Stop-losses checked first (realize losses before new fills)
        2. Limit fills checked second
        """
        newly_filled: list[_OpenTrade] = []
        newly_closed: list[ClosedTradeInfo] = []

        if not self._open_trades and not self._pending_orders:
            return newly_filled, newly_closed

        # 1. Check stop-losses on open trades
        trades_to_close: list[_OpenTrade] = []
        for trade in self._open_trades:
            if trade.stop_loss is None:
                continue
            if (trade.side == OrderSide.BUY and bar.low <= trade.stop_loss) or (
                trade.side == OrderSide.SELL and bar.high >= trade.stop_loss
            ):
                trades_to_close.append(trade)

        for trade in trades_to_close:
            close_price = trade.stop_loss or 0.0
            if trade.side == OrderSide.BUY:
                pnl = trade.quantity * (close_price - trade.open_price)
            else:
                pnl = trade.quantity * (trade.open_price - close_price)

            pnl = round(pnl, 2)
            self._capital += pnl

            closed_info = ClosedTradeInfo(
                broker_trade_id=trade.broker_trade_id,
                close_price=close_price,
                realized_pnl=pnl,
                close_reason="STOP_LOSS",
            )
            self._closed_trades[trade.broker_trade_id] = closed_info
            newly_closed.append(closed_info)
            self._open_trades.remove(trade)

        # 2. Check limit fills
        orders_to_fill: list[_PendingLimitOrder] = []
        for pending in self._pending_orders:
            if (pending.side == OrderSide.BUY and bar.low <= pending.limit_price) or (
                pending.side == OrderSide.SELL and bar.high >= pending.limit_price
            ):
                orders_to_fill.append(pending)

        for pending in orders_to_fill:
            # Limit orders fill at exact limit price (no spread/slippage)
            fill_price = pending.limit_price
            commission = self._commission_per_trade
            self._total_commission += commission
            self._capital -= commission

            broker_trade_id = str(uuid4())
            open_trade = _OpenTrade(
                broker_trade_id=broker_trade_id,
                symbol=pending.symbol,
                side=pending.side,
                quantity=pending.quantity,
                open_price=fill_price,
                stop_loss=pending.stop_loss,
                grid_level_key=pending.grid_level_key,
            )
            self._open_trades.append(open_trade)
            newly_filled.append(open_trade)
            self._pending_orders.remove(pending)

        return newly_filled, newly_closed

    def close_trade(self, broker_trade_id: str) -> None:
        """Close a specific open trade at current market price."""
        trade = next(
            (t for t in self._open_trades if t.broker_trade_id == broker_trade_id),
            None,
        )
        if trade is None:
            msg = f"Trade {broker_trade_id} not found"
            raise RuntimeError(msg)

        assert self._current_bar is not None
        close_side = OrderSide.SELL if trade.side == OrderSide.BUY else OrderSide.BUY
        close_price = self._calculate_fill_price(close_side)

        if trade.side == OrderSide.BUY:
            pnl = trade.quantity * (close_price - trade.open_price)
        else:
            pnl = trade.quantity * (trade.open_price - close_price)

        pnl = round(pnl, 2)
        self._capital += pnl

        closed_info = ClosedTradeInfo(
            broker_trade_id=trade.broker_trade_id,
            close_price=close_price,
            realized_pnl=pnl,
            close_reason="MARKET_CLOSE",
        )
        self._closed_trades[trade.broker_trade_id] = closed_info
        self._open_trades.remove(trade)

    def cancel_order(self, order_id: UUID) -> bool:
        """Cancel a pending limit order by ID."""
        for i, pending in enumerate(self._pending_orders):
            if pending.broker_order_id == str(order_id):
                self._pending_orders.pop(i)
                return True
        return False

    def get_positions(self, symbol: str) -> Position | None:
        """Return the current position for a symbol, updated with mark-to-market."""
        position = self._positions.get(symbol)
        if position is None or position.quantity == 0:
            return position

        if self._current_bar is None:
            return position

        # Mark-to-market: update unrealized P&L with current price
        current_price = self._current_bar.close
        unrealized = position.quantity * (current_price - position.average_cost)

        return Position(
            symbol=position.symbol,
            quantity=position.quantity,
            average_cost=position.average_cost,
            market_value=round(position.quantity * current_price, 2),
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=position.realized_pnl,
            timestamp=self._current_bar.timestamp,
        )

    @property
    def equity(self) -> float:
        """Current equity: capital + unrealized P&L on all open trades/positions."""
        unrealized = 0.0
        if self._current_bar is not None:
            if self._grid_mode:
                # Grid mode: compute from individual open trades only
                price = self._current_bar.close
                for trade in self._open_trades:
                    if trade.side == OrderSide.BUY:
                        unrealized += trade.quantity * (price - trade.open_price)
                    else:
                        unrealized += trade.quantity * (trade.open_price - price)
            else:
                # Simple mode: compute from net positions
                for pos in self._positions.values():
                    if pos.quantity != 0:
                        price = self._current_bar.close
                        unrealized += pos.quantity * (price - pos.average_cost)
        return self._capital + unrealized

    @property
    def total_commission(self) -> float:
        """Total commission paid so far."""
        return self._total_commission

    def _calculate_fill_price(self, side: OrderSide) -> float:
        """Calculate fill price with spread and slippage (market orders only)."""
        assert self._current_bar is not None
        mid_price = self._current_bar.close
        half_spread = self._spread / 2.0
        slip = self._rng.uniform(0, self._slippage)

        if side == OrderSide.BUY:
            return round(mid_price + half_spread + slip, 5)
        else:
            return round(mid_price - half_spread - slip, 5)

    def get_open_trades(self, symbol: str) -> list[OpenBrokerTrade]:
        """Return all currently open individual trades for a symbol."""
        return [
            OpenBrokerTrade(
                broker_trade_id=t.broker_trade_id,
                symbol=t.symbol,
                side=t.side,
                quantity=t.quantity,
                open_price=t.open_price,
            )
            for t in self._open_trades
            if t.symbol == symbol
        ]

    def get_closed_trade_details(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Return details of a closed trade."""
        return self._closed_trades.get(broker_trade_id)

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        """Return all pending limit orders for a symbol."""
        return [
            PendingOrder(
                broker_order_id=p.broker_order_id,
                symbol=p.symbol,
                side=p.side,
                quantity=p.quantity,
                limit_price=p.limit_price,
                grid_level_key=p.grid_level_key,
            )
            for p in self._pending_orders
            if p.symbol == symbol
        ]

    def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all pending orders for a symbol. Returns count cancelled."""
        before = len(self._pending_orders)
        self._pending_orders = [p for p in self._pending_orders if p.symbol != symbol]
        return before - len(self._pending_orders)

    def _update_position(
        self, symbol: str, side: OrderSide, quantity: float, price: float
    ) -> None:
        """Update position after a fill, tracking realized P&L."""
        current = self._positions.get(symbol)
        current_qty = current.quantity if current else 0.0
        current_cost = current.average_cost if current else 0.0
        realized_pnl = current.realized_pnl if current else 0.0

        if side == OrderSide.BUY:
            new_qty = current_qty + quantity
            if current_qty < 0:
                # Closing (or reducing) a short — realize P&L
                close_qty = min(quantity, abs(current_qty))
                pnl = close_qty * (current_cost - price)
                realized_pnl += pnl
                self._capital += pnl
            if new_qty != 0:
                if current_qty >= 0:
                    # Adding to long
                    new_avg = (current_qty * current_cost + quantity * price) / new_qty
                elif new_qty > 0:
                    # Flipped from short to long — new cost is the buy price
                    new_avg = price
                else:
                    # Reduced short but still short — keep original cost
                    new_avg = current_cost
            else:
                new_avg = 0.0
        else:
            new_qty = current_qty - quantity
            if current_qty > 0:
                # Closing (or reducing) a long — realize P&L
                close_qty = min(quantity, current_qty)
                pnl = close_qty * (price - current_cost)
                realized_pnl += pnl
                self._capital += pnl
            if new_qty != 0:
                if current_qty <= 0:
                    # Adding to short (or opening short)
                    total_cost = abs(current_qty) * current_cost + quantity * price
                    new_avg = total_cost / abs(new_qty)
                elif new_qty < 0:
                    # Flipped from long to short — new cost is the sell price
                    new_avg = price
                else:
                    # Reduced long but still long — keep original cost
                    new_avg = current_cost
            else:
                new_avg = 0.0

        market_value = new_qty * price if new_qty != 0 else 0.0
        unrealized = new_qty * (price - new_avg) if new_qty != 0 else 0.0

        self._positions[symbol] = Position(
            symbol=symbol,
            quantity=new_qty,
            average_cost=round(new_avg, 5),
            market_value=round(market_value, 2),
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=round(realized_pnl, 2),
        )
