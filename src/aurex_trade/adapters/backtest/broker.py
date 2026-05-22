"""Simulated broker adapter — fills orders with spread and slippage.

Tracks positions, capital, and P&L internally. Deterministic via seeded RNG.
Satisfies BrokerPort Protocol.
"""

from __future__ import annotations

import random
from uuid import UUID

from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import (
    BarData,
    ClosedTradeInfo,
    OpenBrokerTrade,
    Order,
    Position,
    Trade,
)


class SimulatedBrokerAdapter:
    """Simulated broker for backtesting.

    Fills at current_bar.close ± half_spread ± random_slippage.
    Tracks a single position per symbol with full P&L accounting.
    """

    def __init__(
        self,
        initial_capital: float,
        spread: float = 0.015,
        slippage: float = 0.005,
        commission_per_trade: float = 0.0,
        seed: int = 42,
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

    def set_current_bar(self, bar: BarData) -> None:
        """Update the current market bar (called by runner each step)."""
        self._current_bar = bar

    def place_order(self, order: Order) -> Trade:
        """Simulate filling an order at current price with spread + slippage."""
        if self._current_bar is None:
            msg = "No current bar set — call set_current_bar() first"
            raise RuntimeError(msg)

        fill_price = self._calculate_fill_price(order.side)
        commission = self._commission_per_trade
        self._total_commission += commission
        self._capital -= commission

        self._update_position(order.symbol, order.side, order.quantity, fill_price)

        return Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=commission,
            timestamp=self._current_bar.timestamp,
        )

    def cancel_order(self, order_id: UUID) -> bool:
        """Simulated broker fills immediately — cancellation not possible."""
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
        """Current equity: capital + unrealized P&L on all positions."""
        unrealized = 0.0
        if self._current_bar is not None:
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
        """Calculate fill price with spread and slippage."""
        assert self._current_bar is not None
        mid_price = self._current_bar.close
        half_spread = self._spread / 2.0
        slip = self._rng.uniform(0, self._slippage)

        if side == OrderSide.BUY:
            return round(mid_price + half_spread + slip, 5)
        else:
            return round(mid_price - half_spread - slip, 5)

    def get_open_trades(self, symbol: str) -> list[OpenBrokerTrade]:
        """Backtest broker does not track individual open trades."""
        return []

    def get_closed_trade_details(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Backtest broker does not track closed trade details."""
        return None

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
