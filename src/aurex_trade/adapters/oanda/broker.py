"""OANDA broker adapter — implements BrokerPort via v20 REST API."""

from uuid import UUID

import structlog

from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.domain.enums import OrderSide, OrderType
from aurex_trade.domain.models import (
    ClosedTradeInfo,
    OpenBrokerTrade,
    Order,
    PendingOrder,
    Position,
    Trade,
)

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
        """Place an order (MARKET or LIMIT) and return the resulting Trade.

        For MARKET orders: fills immediately, returns Trade with fill price.
        For LIMIT orders: placed as GTC pending order, returns Trade with
        broker_trade_id set to the pending order ID and price=limit_price.
        """
        raw_units = order.quantity if order.side == OrderSide.BUY else -order.quantity
        units = int(raw_units)
        if units == 0:
            msg = f"Order quantity too small to trade: {order.quantity}"
            raise ValueError(msg)

        if order.order_type == OrderType.LIMIT:
            return self._place_limit_order(order, units)

        return self._place_market_order(order, units)

    def _place_market_order(self, order: Order, units: int) -> Trade:
        """Place a FOK market order — fills immediately or raises."""
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
        if order.trailing_stop_distance is not None:
            order_body["trailingStopLossOnFill"] = {
                "distance": f"{order.trailing_stop_distance:.5f}",
            }

        body = {"order": order_body}

        data = self._connection.post(f"/v3/accounts/{self._account_id}/orders", json=body)

        fill = data.get("orderFillTransaction")
        if fill is None:
            cancel = data.get("orderCancelTransaction", {})
            reason = cancel.get("reason", "UNKNOWN")
            msg = f"OANDA order not filled: {reason}"
            log.warning(
                "oanda_order_not_filled",
                symbol=order.symbol,
                side=order.side.value,
                reason=reason,
                response_keys=list(data.keys()),
            )
            raise RuntimeError(msg)

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

    def _place_limit_order(self, order: Order, units: int) -> Trade:
        """Place a GTC limit order — returns immediately with pending order ID."""
        if order.limit_price is None:
            msg = "Limit order requires a limit_price"
            raise ValueError(msg)

        order_body: dict[str, str | dict[str, str]] = {
            "type": "LIMIT",
            "instrument": order.symbol,
            "units": str(units),
            "price": f"{order.limit_price:.5f}",
            "timeInForce": "GTC",
            "positionFill": "DEFAULT",
            "triggerCondition": "DEFAULT",
        }

        if order.stop_loss is not None:
            order_body["stopLossOnFill"] = {"price": f"{order.stop_loss:.5f}"}
        if order.take_profit is not None:
            order_body["takeProfitOnFill"] = {"price": f"{order.take_profit:.5f}"}
        if order.trailing_stop_distance is not None:
            order_body["trailingStopLossOnFill"] = {
                "distance": f"{order.trailing_stop_distance:.5f}",
            }

        body = {"order": order_body}

        data = self._connection.post(f"/v3/accounts/{self._account_id}/orders", json=body)

        # OANDA returns orderCreateTransaction for pending orders
        create_txn = data.get("orderCreateTransaction")
        if create_txn is None:
            cancel = data.get("orderCancelTransaction", {})
            reason = cancel.get("reason", "UNKNOWN")
            msg = f"OANDA limit order rejected: {reason}"
            log.warning("oanda_limit_order_rejected", reason=reason)
            raise RuntimeError(msg)

        broker_order_id = create_txn["id"]

        # Check if the limit order was immediately cancelled (e.g. price already
        # past limit in wrong direction, or trigger condition not met)
        cancel_txn = data.get("orderCancelTransaction")
        if cancel_txn is not None:
            reason = cancel_txn.get("reason", "UNKNOWN")
            log.warning(
                "oanda_limit_order_immediately_cancelled",
                symbol=order.symbol,
                side=order.side.value,
                limit_price=order.limit_price,
                reason=reason,
                broker_order_id=broker_order_id,
            )
            msg = f"OANDA limit order immediately cancelled: {reason}"
            raise RuntimeError(msg)

        # Check if the limit order filled immediately (price was already marketable)
        fill_txn = data.get("orderFillTransaction")
        if fill_txn is not None:
            trade_opened = fill_txn.get("tradeOpened")
            broker_trade_id = trade_opened["tradeID"] if trade_opened else broker_order_id
            fill_price = float(fill_txn["price"])
            log.info(
                "oanda_limit_order_filled_immediately",
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                limit_price=order.limit_price,
                fill_price=fill_price,
                broker_trade_id=broker_trade_id,
            )
            return Trade(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=fill_price,
                commission=0.0,
                broker_trade_id=broker_trade_id,
                immediately_filled=True,
            )

        log.info(
            "oanda_limit_order_placed",
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            limit_price=order.limit_price,
            broker_order_id=broker_order_id,
        )

        # Return Trade with order ID as broker_trade_id (pending, not yet filled)
        return Trade(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.limit_price,
            commission=0.0,
            broker_trade_id=broker_order_id,
        )

    def cancel_order(self, order_id: UUID) -> bool:
        """Cancel an order. Market FOK orders fill immediately — always returns False."""
        log.debug("oanda_cancel_noop", order_id=str(order_id))
        return False

    def get_positions(self, symbol: str) -> Position | None:
        """Return the current net position for a symbol, or None if flat."""
        from aurex_trade.adapters.oanda.connection import OANDAAPIError

        try:
            data = self._connection.get(f"/v3/accounts/{self._account_id}/positions/{symbol}")
        except OANDAAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

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
            log.info(
                "oanda_trade_not_closed",
                broker_trade_id=broker_trade_id,
                state=trade.get("state") if trade else "missing",
                keys=list(trade.keys()) if trade else [],
            )
            return None

        log.info(
            "oanda_closed_trade_raw",
            broker_trade_id=broker_trade_id,
            keys=list(trade.keys()),
            state=trade.get("state"),
            average_close_price=trade.get("averageClosePrice"),
            realized_pl=trade.get("realizedPL"),
            close_reason=trade.get("closeReason"),
        )

        close_reason = trade.get("closeReason") or ""
        # Map OANDA close reasons to simplified labels
        if "TAKE_PROFIT" in close_reason:
            reason = "TAKE_PROFIT"
        elif "STOP_LOSS" in close_reason:
            reason = "STOP_LOSS"
        elif close_reason:
            reason = close_reason
        else:
            # closeReason missing — infer from attached order states
            tp_order = trade.get("takeProfitOrder", {})
            sl_order = trade.get("stopLossOrder", {})
            if tp_order.get("state") == "FILLED":
                reason = "TAKE_PROFIT"
            elif sl_order.get("state") == "FILLED":
                reason = "STOP_LOSS"
            else:
                reason = "UNKNOWN"

        return ClosedTradeInfo(
            broker_trade_id=broker_trade_id,
            close_price=float(trade.get("averageClosePrice", 0.0)),
            realized_pnl=float(trade.get("realizedPL", 0.0)),
            close_reason=reason,
        )

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        """Return all pending (unfilled) limit orders for a symbol."""
        data = self._connection.get(f"/v3/accounts/{self._account_id}/pendingOrders")
        orders: list[PendingOrder] = []
        for o in data.get("orders", []):
            if o.get("instrument") != symbol:
                continue
            if o.get("type") != "LIMIT":
                continue
            units = float(o["units"])
            side = OrderSide.BUY if units > 0 else OrderSide.SELL
            orders.append(
                PendingOrder(
                    broker_order_id=o["id"],
                    symbol=symbol,
                    side=side,
                    quantity=abs(units),
                    limit_price=float(o["price"]),
                )
            )
        return orders

    def close_trade(self, broker_trade_id: str) -> None:
        """Close a specific trade using OANDA's dedicated close endpoint.

        Uses PUT /trades/{id}/close which does NOT require margin (unlike
        placing a counter-order). Raises on failure.
        """
        self._connection.put(
            f"/v3/accounts/{self._account_id}/trades/{broker_trade_id}/close",
            json={"units": "ALL"},
        )
        log.info("oanda_trade_closed", broker_trade_id=broker_trade_id)

    def set_trailing_stop(self, broker_trade_id: str, distance: float) -> None:
        """Add or replace a trailing stop loss on an existing open trade."""
        body = {
            "trailingStopLoss": {
                "distance": f"{distance:.5f}",
                "timeInForce": "GTC",
            },
        }
        self._connection.put(
            f"/v3/accounts/{self._account_id}/trades/{broker_trade_id}/orders",
            json=body,
        )
        log.info(
            "oanda_trailing_stop_set",
            broker_trade_id=broker_trade_id,
            distance=distance,
        )

    def cancel_all_orders(self, symbol: str) -> int:
        """Cancel all pending orders for a symbol. Returns count cancelled."""
        pending = self.get_pending_orders(symbol)
        count = 0
        for order in pending:
            try:
                self._connection.put(
                    f"/v3/accounts/{self._account_id}/orders/{order.broker_order_id}/cancel"
                )
                count += 1
            except Exception:
                log.warning(
                    "oanda_cancel_order_failed",
                    broker_order_id=order.broker_order_id,
                )
        if count:
            log.info("oanda_orders_cancelled", symbol=symbol, count=count)
        return count
