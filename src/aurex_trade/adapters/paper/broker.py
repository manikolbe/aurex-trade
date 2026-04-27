"""Paper broker adapter — simulates order fills and price data for local mode.

No external dependencies. Generates deterministic-ish price data using a
random walk seeded per symbol, and fills orders at the latest simulated price.
"""

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import BarData, Order, Position, Trade


class PaperBrokerAdapter:
    """Simulated broker for local mode development.

    Implements both BrokerPort and MarketDataPort Protocols.
    Tracks positions in memory and simulates fills at the current market price.
    """

    def __init__(self, base_price: float = 180.0, seed: int | None = None) -> None:
        self._base_price = base_price
        self._rng = random.Random(seed)  # noqa: S311
        self._positions: dict[str, Position] = {}
        self._price_history: dict[str, list[BarData]] = {}

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

        return Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=0.0,
        )

    def cancel_order(self, order_id: UUID) -> bool:
        """Paper broker fills immediately, so cancellation always returns False."""
        return False

    def get_positions(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

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
        """Update in-memory position after a fill."""
        current = self._positions.get(symbol)
        current_qty = current.quantity if current else 0.0
        current_cost = current.average_cost if current else 0.0
        realized_pnl = current.realized_pnl if current else 0.0

        if side == OrderSide.BUY:
            new_qty = current_qty + quantity
            if new_qty != 0:
                new_avg = (current_qty * current_cost + quantity * price) / new_qty
            else:
                new_avg = 0.0
        else:
            new_qty = current_qty - quantity
            # Realize P&L on the sold portion
            realized_pnl += quantity * (price - current_cost)
            new_avg = current_cost if new_qty != 0 else 0.0

        self._positions[symbol] = Position(
            symbol=symbol,
            quantity=new_qty,
            average_cost=round(new_avg, 4),
            market_value=round(new_qty * price, 2),
            unrealized_pnl=round(new_qty * (price - new_avg), 2),
            realized_pnl=round(realized_pnl, 2),
        )
