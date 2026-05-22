"""OANDA broker adapter — implements BrokerPort via v20 REST API."""

from uuid import UUID

import structlog

from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import ClosedTradeInfo, OpenBrokerTrade, Order, Position, Trade

log = structlog.get_logger()


class OANDABrokerAdapter:
    """Place orders and query positions via the OANDA v20 REST API.

    OANDA uses negative units for sell orders (no separate side field).
    Market orders use Fill-or-Kill (FOK) time-in-force for immediate execution.
    Commission is zero — OANDA charges via the bid/ask spread.
    """

    def __init__(self, connection: OANDAConnection, account_id: str) -> None:
        self._connection = connection
        self._account_id = account_id

    @property
    def equity(self) -> float:
        """Return account NAV (balance + unrealized P&L) from OANDA."""
        data = self._connection.get(f"/v3/accounts/{self._account_id}/summary")
        return float(data["account"]["NAV"])

    def get_account_summary(self) -> dict[str, float | int]:
        """Return account balance, unrealized P&L, and open position count."""
        data = self._connection.get(f"/v3/accounts/{self._account_id}/summary")
        account = data["account"]
        return {
            "balance": float(account["balance"]),
            "unrealized_pnl": float(account.get("unrealizedPL", 0.0)),
            "open_position_count": int(account.get("openPositionCount", 0)),
        }

    def place_order(self, order: Order) -> Trade:
        """Place a market order and return the resulting Trade."""
        raw_units = order.quantity if order.side == OrderSide.BUY else -order.quantity
        # OANDA requires integer units for most instruments (including XAU_USD)
        units = int(raw_units)
        if units == 0:
            msg = f"Order quantity too small to trade: {order.quantity}"
            raise ValueError(msg)

        order_body: dict[str, str | dict[str, str]] = {
            "type": "MARKET",
            "instrument": order.symbol,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
        }

        if order.stop_loss is not None:
            order_body["stopLossOnFill"] = {"price": f"{order.stop_loss:.5f}"}
        if order.take_profit is not None:
            order_body["takeProfitOnFill"] = {"price": f"{order.take_profit:.5f}"}

        body = {"order": order_body}

        data = self._connection.post(f"/v3/accounts/{self._account_id}/orders", json=body)

        fill = data["orderFillTransaction"]

        # OANDA returns tradeOpened only when a new trade is created.
        # Position-reducing fills (closing/flipping) don't open a new trade.
        trade_opened = fill.get("tradeOpened")
        broker_trade_id = trade_opened["tradeID"] if trade_opened else ""

        trade = Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=float(fill["price"]),
            commission=0.0,
            broker_trade_id=broker_trade_id,
        )

        log.info(
            "oanda_order_filled",
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            price=trade.price,
        )
        return trade

    def cancel_order(self, order_id: UUID) -> bool:
        """Cancel an order. Market FOK orders fill immediately — always returns False."""
        log.debug("oanda_cancel_noop", order_id=str(order_id))
        return False

    def get_positions(self, symbol: str) -> Position | None:
        """Return the current net position for a symbol, or None if flat."""
        data = self._connection.get(f"/v3/accounts/{self._account_id}/positions/{symbol}")

        pos = data["position"]
        long_units = float(pos["long"]["units"])
        short_units = float(pos["short"]["units"])
        net_units = long_units + short_units  # short units are negative

        if net_units == 0.0:
            return None

        # Use the side that has units for average price and P&L
        side_data = pos["long"] if net_units > 0.0 else pos["short"]

        avg_price = float(side_data.get("averagePrice", 0.0))
        unrealized_pnl = float(side_data.get("unrealizedPL", 0.0))
        realized_pnl = float(pos.get("pl", 0.0))

        position = Position(
            symbol=symbol,
            quantity=net_units,
            average_cost=avg_price,
            market_value=abs(net_units) * avg_price,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
        )

        log.debug(
            "oanda_position_fetched",
            symbol=symbol,
            quantity=net_units,
            unrealized_pnl=unrealized_pnl,
        )
        return position

    def get_open_trades(self, symbol: str) -> list[OpenBrokerTrade]:
        """Return all currently open trades for a symbol."""
        data = self._connection.get(
            f"/v3/accounts/{self._account_id}/openTrades",
        )
        trades: list[OpenBrokerTrade] = []
        for t in data.get("trades", []):
            if t["instrument"] != symbol:
                continue
            units = float(t["currentUnits"])
            side = OrderSide.BUY if units > 0 else OrderSide.SELL
            trades.append(
                OpenBrokerTrade(
                    broker_trade_id=t["id"],
                    symbol=t["instrument"],
                    side=side,
                    quantity=abs(units),
                    open_price=float(t["price"]),
                )
            )
        return trades

    def get_closed_trade_details(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Query OANDA for details of a closed trade."""
        data = self._connection.get(
            f"/v3/accounts/{self._account_id}/trades/{broker_trade_id}",
        )
        trade = data.get("trade")
        if trade is None or trade.get("state") != "CLOSED":
            return None

        log.debug(
            "oanda_closed_trade_raw",
            broker_trade_id=broker_trade_id,
            keys=list(trade.keys()),
            state=trade.get("state"),
            average_close_price=trade.get("averageClosePrice"),
            realized_pl=trade.get("realizedPL"),
            close_reason=trade.get("closeReason"),
        )

        close_reason = trade.get("closeReason", "UNKNOWN")
        # Map OANDA close reasons to simplified labels
        if "TAKE_PROFIT" in close_reason:
            reason = "TAKE_PROFIT"
        elif "STOP_LOSS" in close_reason:
            reason = "STOP_LOSS"
        else:
            reason = close_reason

        return ClosedTradeInfo(
            broker_trade_id=broker_trade_id,
            close_price=float(trade.get("averageClosePrice", 0.0)),
            realized_pnl=float(trade.get("realizedPL", 0.0)),
            close_reason=reason,
        )
