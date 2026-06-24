"""Paper broker adapter — simulates order fills and price data for local mode.

No external dependencies. Generates deterministic-ish price data using a
random walk seeded per symbol, and fills orders at the latest simulated price.
"""

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import (
    BarData,
    ClosedTradeInfo,
    OpenBrokerTrade,
    Order,
    PendingOrder,
    Position,
    Trade,
)


class PaperBrokerAdapter:
    """Simulated broker for local mode development.

    Implements both BrokerPort and MarketDataPort Protocols.
    Tracks positions in memory and simulates fills at the current market price.
    """

    def __init__(
        self,
        base_price: float = 180.0,
        seed: int | None = None,
        initial_capital: float = 100_000.0,
    ) -> None:
        self._base_price = base_price
        self._rng = random.Random(seed)  # noqa: S311
        self._positions: dict[str, Position] = {}
        self._price_history: dict[str, list[BarData]] = {}
        self._capital = initial_capital
        self._open_trades: dict[str, OpenBrokerTrade] = {}
        self._trade_counter = 0

    @property
    def equity(self) -> float:
        """Current equity: capital + unrealized P&L on all open positions."""
        unrealized = sum(pos.unrealized_pnl for pos in self._positions.values())
        return self._capital + unrealized

    # -- MarketDataPort --

    def get_latest_bars(self, symbol: str, count: int) -> list[BarData]:
        """Return simulated OHLCV bars. Generates more if needed."""
        history = self._price_history.get(symbol, [])

        while len(history) < count:
            bar = self._generate_bar(symbol, len(history))
            history.append(bar)

        self._price_history[symbol] = history
        return history[-count:]

    # -- BrokerPort --

    def place_order(self, order: Order) -> Trade:
        """Simulate an immediate fill at the latest market price."""
        bars = self.get_latest_bars(order.symbol, 1)
        fill_price = bars[-1].close

        self._update_position(order.symbol, order.side, order.quantity, fill_price)

        self._trade_counter += 1
        broker_trade_id = str(self._trade_counter)

        self._open_trades[broker_trade_id] = OpenBrokerTrade(
            broker_trade_id=broker_trade_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            open_price=fill_price,
        )

        return Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=0.0,
            broker_trade_id=broker_trade_id,
        )

    def cancel_order(self, order_id: UUID) -> bool:
        """Paper broker fills immediately, so cancellation always returns False."""
        return False

    def get_positions(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def get_open_trades(self, symbol: str) -> list[OpenBrokerTrade]:
        """Return all open trades for a symbol."""
        return [t for t in self._open_trades.values() if t.symbol == symbol]

    def get_closed_trade_details(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Paper broker does not track closed trade details."""
        return None

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        """Paper broker has no pending orders."""
        return []

    def close_trade(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Close a specific open trade by removing it from tracked trades.

        Paper broker does not track per-trade realized P&L, so returns None.
        """
        if broker_trade_id not in self._open_trades:
            msg = f"Trade {broker_trade_id} not found"
            raise RuntimeError(msg)
        del self._open_trades[broker_trade_id]
        return None

    def set_trailing_stop(self, broker_trade_id: str, distance: float) -> None:
        """Paper broker — trailing stop is tracked but not simulated."""

    def cancel_all_orders(self, symbol: str) -> int:
        """Paper broker has no pending orders to cancel."""
        return 0

    def cancel_pending_order(self, broker_order_id: str) -> bool:
        """Paper broker has no pending orders to cancel."""
        return False

    # -- Internal --

    def _generate_bar(self, symbol: str, index: int) -> BarData:
        """Generate a single simulated OHLCV bar using a random walk."""
        now = datetime.now(UTC)
        timestamp = now - timedelta(minutes=(1000 - index))

        # Random walk: small percentage changes
        drift = self._rng.gauss(0.0, 0.002)
        history = self._price_history.get(symbol, [])
        prev_close = history[-1].close if history else self._base_price
        close = prev_close * (1 + drift)

        # Generate OHLV around close
        spread = abs(close * self._rng.gauss(0.0, 0.001))
        open_price = close + self._rng.uniform(-spread, spread)
        high = max(open_price, close) + abs(spread)
        low = min(open_price, close) - abs(spread)
        volume = self._rng.uniform(500.0, 5000.0)

        return BarData(
            timestamp=timestamp,
            open=round(open_price, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(close, 2),
            volume=round(volume, 2),
            symbol=symbol,
        )

    def _update_position(
        self, symbol: str, side: OrderSide, quantity: float, price: float
    ) -> None:
        """Update in-memory position after a fill.

        Mirrors SimulatedBrokerAdapter logic: only realize P&L when closing
        (or reducing) an existing position, not when opening.
        """
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
                    # Adding to long (or opening long)
                    new_avg = (current_qty * current_cost + quantity * price) / new_qty
                elif new_qty > 0:
                    # Flipped from short to long — new cost is buy price
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
                    # Flipped from long to short — new cost is sell price
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
            average_cost=round(new_avg, 4),
            market_value=round(market_value, 2),
            unrealized_pnl=round(unrealized, 2),
            realized_pnl=round(realized_pnl, 2),
        )
