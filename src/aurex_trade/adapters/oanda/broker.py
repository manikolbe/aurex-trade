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

        if order.order_type == OrderType.STOP:
            return self._place_stop_order(order, units)

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

    def _place_stop_order(self, order: Order, units: int) -> Trade:
        """Place a GTC stop entry order — returns immediately with pending order ID.

        A stop entry order fills when the market trades through the trigger price
        (a BUY stop above the market, a SELL stop below it). Unlike a limit, it
        does not fill early when price is already favourable — which is exactly
        why grid levels above the current price use a BUY stop and levels below
        use a SELL stop. Mirrors the limit-order flow otherwise.
        """
        if order.limit_price is None:
            msg = "Stop order requires a limit_price (the trigger price)"
            raise ValueError(msg)

        order_body: dict[str, str | dict[str, str]] = {
            "type": "STOP",
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

        create_txn = data.get("orderCreateTransaction")
        if create_txn is None:
            cancel = data.get("orderCancelTransaction", {})
            reason = cancel.get("reason", "UNKNOWN")
            msg = f"OANDA stop order rejected: {reason}"
            log.warning("oanda_stop_order_rejected", reason=reason)
            raise RuntimeError(msg)

        broker_order_id = create_txn["id"]

        # Check if the stop order was immediately cancelled (e.g. trigger price
        # on the wrong side of the market, or trigger condition not met).
        cancel_txn = data.get("orderCancelTransaction")
        if cancel_txn is not None:
            reason = cancel_txn.get("reason", "UNKNOWN")
            log.warning(
                "oanda_stop_order_immediately_cancelled",
                symbol=order.symbol,
                side=order.side.value,
                trigger_price=order.limit_price,
                reason=reason,
                broker_order_id=broker_order_id,
            )
            msg = f"OANDA stop order immediately cancelled: {reason}"
            raise RuntimeError(msg)

        # Check if the stop order filled immediately (price already through trigger).
        fill_txn = data.get("orderFillTransaction")
        if fill_txn is not None:
            trade_opened = fill_txn.get("tradeOpened")
            broker_trade_id = trade_opened["tradeID"] if trade_opened else broker_order_id
            fill_price = float(fill_txn["price"])
            log.info(
                "oanda_stop_order_filled_immediately",
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                trigger_price=order.limit_price,
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
            "oanda_stop_order_placed",
            symbol=order.symbol,
            side=order.side.value,
            quantity=order.quantity,
            trigger_price=order.limit_price,
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

    # How far back (in transactions) to scan for the closing fill of a trade.
    # The fast-poll runs every few seconds, so closures are found well within
    # this window; it bounds the request without paginating the whole history.
    _CLOSE_LOOKBACK_TXNS: int = 200

    def get_closed_trade_details(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Find the closing fill for a trade via the transactions feed.

        ``GET /trades/{id}`` does not reliably resolve a just-closed trade (it
        returns 404), so the authoritative source is the ORDER_FILL transaction
        that closed it: it carries the realized P&L (incl. slippage), the exit
        price, and the close reason, keyed by tradeID under ``tradesClosed``.
        """
        # Bound the scan to the most recent transactions.
        try:
            summary = self._connection.get(
                f"/v3/accounts/{self._account_id}/summary"
            )
            last_txn_id = int(summary["account"]["lastTransactionID"])
        except Exception:
            log.warning("oanda_last_txn_id_unavailable", broker_trade_id=broker_trade_id)
            return None

        since_id = max(1, last_txn_id - self._CLOSE_LOOKBACK_TXNS)
        data = self._connection.get(
            f"/v3/accounts/{self._account_id}/transactions/sinceid",
            params={"id": str(since_id), "type": "ORDER_FILL"},
        )

        # Find the fill that closed this trade (most recent match wins).
        match: dict[str, object] | None = None
        closed_entry: dict[str, object] | None = None
        for txn in data.get("transactions", []):
            for entry in txn.get("tradesClosed", []) or []:
                if entry.get("tradeID") == broker_trade_id:
                    match = txn
                    closed_entry = entry
        if match is None or closed_entry is None:
            log.info("oanda_closing_fill_not_found", broker_trade_id=broker_trade_id)
            return None

        # Map the closing order reason to a simplified label. Check TRAILING
        # before STOP (OANDA's "TRAILING_STOP_LOSS_ORDER" also contains "STOP").
        fill_reason = str(match.get("reason", ""))
        if "TAKE_PROFIT" in fill_reason:
            reason = "TAKE_PROFIT"
        elif "TRAILING_STOP" in fill_reason:
            reason = "TRAILING_STOP"
        elif "STOP_LOSS" in fill_reason:
            reason = "STOP_LOSS"
        else:
            reason = fill_reason or "UNKNOWN"

        realized_pnl = float(str(closed_entry.get("realizedPL", match.get("pl", "0.0"))))
        close_price = float(str(closed_entry.get("price", match.get("price", "0.0"))))

        log.info(
            "oanda_closing_fill_found",
            broker_trade_id=broker_trade_id,
            realized_pnl=realized_pnl,
            close_price=close_price,
            close_reason=reason,
        )
        return ClosedTradeInfo(
            broker_trade_id=broker_trade_id,
            close_price=close_price,
            realized_pnl=realized_pnl,
            close_reason=reason,
        )

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        """Return all pending (unfilled) entry orders (LIMIT or STOP) for a symbol.

        Grid strategies rest both LIMIT and STOP entry orders. Both must be
        reported here — otherwise the engine's fill detection sees a resting STOP
        "disappear" from the pending list and wrongly treats it as cancelled,
        causing a place/cancel churn loop.
        """
        data = self._connection.get(f"/v3/accounts/{self._account_id}/pendingOrders")
        orders: list[PendingOrder] = []
        for o in data.get("orders", []):
            if o.get("instrument") != symbol:
                continue
            if o.get("type") not in ("LIMIT", "STOP"):
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

    def cancel_pending_order(self, broker_order_id: str) -> bool:
        """Cancel a single pending order by its broker order ID. Returns success."""
        try:
            self._connection.put(
                f"/v3/accounts/{self._account_id}/orders/{broker_order_id}/cancel"
            )
        except Exception:
            log.warning("oanda_cancel_pending_order_failed", broker_order_id=broker_order_id)
            return False
        log.info("oanda_pending_order_cancelled", broker_order_id=broker_order_id)
        return True

    def close_trade(self, broker_trade_id: str) -> ClosedTradeInfo | None:
        """Close a specific trade using OANDA's dedicated close endpoint.

        Uses PUT /trades/{id}/close which does NOT require margin (unlike
        placing a counter-order). Raises on failure.

        Returns the realized P&L / close details parsed directly from the close
        response's ``orderFillTransaction`` (which carries ``tradesClosed[]`` with
        realizedPL + price, and the fill ``reason``). This avoids the transactions
        history endpoint, which 504s on long-lived accounts. Returns None if the
        response lacks a closing fill (e.g. trade already gone).
        """
        data = self._connection.put(
            f"/v3/accounts/{self._account_id}/trades/{broker_trade_id}/close",
            json={"units": "ALL"},
        )
        log.info("oanda_trade_closed", broker_trade_id=broker_trade_id)
        return self._parse_close_fill(broker_trade_id, data)

    @staticmethod
    def _parse_close_fill(
        broker_trade_id: str, data: dict[str, object]
    ) -> ClosedTradeInfo | None:
        """Extract ClosedTradeInfo from a PUT /close orderFillTransaction body."""
        fill = data.get("orderFillTransaction")
        if not isinstance(fill, dict):
            return None

        # Locate this trade in tradesClosed[] for its exact realizedPL + price.
        closed_entry: dict[str, object] | None = None
        for entry in fill.get("tradesClosed", []) or []:
            if isinstance(entry, dict) and entry.get("tradeID") == broker_trade_id:
                closed_entry = entry
                break
        if closed_entry is None:
            return None

        fill_reason = str(fill.get("reason", ""))
        if "TAKE_PROFIT" in fill_reason:
            reason = "TAKE_PROFIT"
        elif "TRAILING_STOP" in fill_reason:
            reason = "TRAILING_STOP"
        elif "STOP_LOSS" in fill_reason:
            reason = "STOP_LOSS"
        else:
            # A deliberate close (margin trim / close-all) fills via MARKET_ORDER;
            # keep OANDA's verbatim reason, falling back to a generic label.
            reason = fill_reason or "MARKET_CLOSE"

        realized_pnl = float(str(closed_entry.get("realizedPL", fill.get("pl", "0.0"))))
        close_price = float(str(closed_entry.get("price", fill.get("price", "0.0"))))
        return ClosedTradeInfo(
            broker_trade_id=broker_trade_id,
            close_price=close_price,
            realized_pnl=realized_pnl,
            close_reason=reason,
        )

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
