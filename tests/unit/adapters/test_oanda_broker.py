"""Tests for the OANDA broker adapter."""

from unittest.mock import MagicMock
from uuid import uuid4

from aurex_trade.adapters.oanda.broker import OANDABrokerAdapter
from aurex_trade.adapters.oanda.connection import OANDAAPIError, OANDAConnection
from aurex_trade.domain.enums import OrderSide, OrderType
from aurex_trade.domain.models import Order


def _make_pending_response(order_id: str = "7001") -> dict:  # type: ignore[type-arg]
    """Build an OANDA response for a resting (pending) entry order."""
    return {"orderCreateTransaction": {"id": order_id, "type": "STOP_ORDER"}}


def _make_fill_response(price: str = "2050.50", units: str = "10") -> dict:  # type: ignore[type-arg]
    """Build a realistic OANDA order fill response."""
    return {
        "orderFillTransaction": {
            "id": "6368",
            "type": "ORDER_FILL",
            "instrument": "XAU_USD",
            "units": units,
            "price": price,
            "tradeOpened": {"tradeID": "6368", "units": units},
            "accountBalance": "100000.0",
        }
    }


def _make_position_response(
    long_units: str = "0",
    short_units: str = "0",
    long_avg: str = "0",
    short_avg: str = "0",
    long_pnl: str = "0",
    short_pnl: str = "0",
    realized_pnl: str = "0",
) -> dict:  # type: ignore[type-arg]
    """Build a realistic OANDA position response."""
    return {
        "position": {
            "instrument": "XAU_USD",
            "long": {
                "units": long_units,
                "averagePrice": long_avg,
                "unrealizedPL": long_pnl,
            },
            "short": {
                "units": short_units,
                "averagePrice": short_avg,
                "unrealizedPL": short_pnl,
            },
            "pl": realized_pnl,
            "unrealizedPL": long_pnl,
        }
    }


class TestPlaceOrder:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_buy_sends_positive_units(self) -> None:
        self.conn.post.return_value = _make_fill_response(price="2050.50", units="10")
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=10.0)
        self.adapter.place_order(order)

        call_args = self.conn.post.call_args
        body = call_args[1]["json"]
        assert body["order"]["units"] == "10"

    def test_sell_sends_negative_units(self) -> None:
        self.conn.post.return_value = _make_fill_response(price="2050.50", units="-10")
        order = Order(symbol="XAU_USD", side=OrderSide.SELL, quantity=10.0)
        self.adapter.place_order(order)

        call_args = self.conn.post.call_args
        body = call_args[1]["json"]
        assert body["order"]["units"] == "-10"

    def test_returns_trade_with_fill_price(self) -> None:
        self.conn.post.return_value = _make_fill_response(price="2050.50")
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=5.0)
        trade = self.adapter.place_order(order)

        assert trade.price == 2050.50
        assert trade.order_id == order.id
        assert trade.symbol == "XAU_USD"
        assert trade.side == OrderSide.BUY
        assert trade.quantity == 5.0
        assert trade.commission == 0.0

    def test_posts_to_correct_endpoint(self) -> None:
        self.conn.post.return_value = _make_fill_response()
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0)
        self.adapter.place_order(order)

        self.conn.post.assert_called_once()
        call_args = self.conn.post.call_args
        assert call_args[0][0] == "/v3/accounts/101-001-123/orders"

    def test_uses_market_order_type(self) -> None:
        self.conn.post.return_value = _make_fill_response()
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0)
        self.adapter.place_order(order)

        body = self.conn.post.call_args[1]["json"]
        assert body["order"]["type"] == "MARKET"
        assert body["order"]["timeInForce"] == "FOK"

    def test_api_error_propagates(self) -> None:
        self.conn.post.side_effect = OANDAAPIError(400, "Invalid order")
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0)
        try:
            self.adapter.place_order(order)
            assert False, "Should have raised"  # noqa: B011
        except OANDAAPIError:
            pass


class TestPlaceStopOrder:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_sends_stop_order_type(self) -> None:
        self.conn.post.return_value = _make_pending_response()
        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,
            stop_loss=4099.90,
        )
        self.adapter.place_order(order)

        body = self.conn.post.call_args[1]["json"]["order"]
        assert body["type"] == "STOP"
        assert body["price"] == "4115.90000"
        assert body["units"] == "20"
        assert body["timeInForce"] == "GTC"
        assert body["stopLossOnFill"] == {"price": "4099.90000"}

    def test_returns_pending_trade_with_order_id(self) -> None:
        self.conn.post.return_value = _make_pending_response(order_id="7042")
        order = Order(
            symbol="XAU_USD",
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4085.00,
        )
        trade = self.adapter.place_order(order)

        assert trade.broker_trade_id == "7042"
        assert trade.price == 4085.00  # placement price, not yet filled

    def test_missing_trigger_price_raises(self) -> None:
        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=None,
        )
        try:
            self.adapter.place_order(order)
            assert False, "Should have raised"  # noqa: B011
        except ValueError:
            pass

    def test_immediate_fill_returns_filled_trade(self) -> None:
        """If price is already through the trigger, OANDA fills on placement."""
        self.conn.post.return_value = {
            "orderCreateTransaction": {"id": "7100", "type": "STOP_ORDER"},
            "orderFillTransaction": {
                "id": "7101",
                "price": "4116.20",
                "tradeOpened": {"tradeID": "7101", "units": "20"},
            },
        }
        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,
        )
        trade = self.adapter.place_order(order)

        assert trade.immediately_filled is True
        assert trade.broker_trade_id == "7101"
        assert trade.price == 4116.20

    def test_immediate_cancel_raises(self) -> None:
        """A stop cancelled at placement (e.g. wrong side of market) raises."""
        self.conn.post.return_value = {
            "orderCreateTransaction": {"id": "7200", "type": "STOP_ORDER"},
            "orderCancelTransaction": {"reason": "PRICE_PRECISION_EXCEEDED"},
        }
        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,
        )
        try:
            self.adapter.place_order(order)
            assert False, "Should have raised"  # noqa: B011
        except RuntimeError:
            pass

    def test_rejected_stop_raises(self) -> None:
        """No orderCreateTransaction at all means OANDA rejected the order."""
        self.conn.post.return_value = {
            "orderCancelTransaction": {"reason": "INSUFFICIENT_MARGIN"},
        }
        order = Order(
            symbol="XAU_USD",
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            quantity=20.0,
            limit_price=4115.90,
        )
        try:
            self.adapter.place_order(order)
            assert False, "Should have raised"  # noqa: B011
        except RuntimeError:
            pass

    def test_cancel_pending_order_puts_to_cancel_endpoint(self) -> None:
        self.conn.put.return_value = {}
        ok = self.adapter.cancel_pending_order("7042")
        assert ok is True
        self.conn.put.assert_called_once_with(
            "/v3/accounts/101-001-123/orders/7042/cancel"
        )

    def test_cancel_pending_order_returns_false_on_error(self) -> None:
        self.conn.put.side_effect = OANDAAPIError(404, "Order not found")
        assert self.adapter.cancel_pending_order("nope") is False


class TestStopLossAndTakeProfit:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_stop_loss_included_when_set(self) -> None:
        self.conn.post.return_value = _make_fill_response()
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0, stop_loss=2040.12345)
        self.adapter.place_order(order)

        body = self.conn.post.call_args[1]["json"]
        assert body["order"]["stopLossOnFill"] == {"price": "2040.12345"}

    def test_take_profit_included_when_set(self) -> None:
        self.conn.post.return_value = _make_fill_response()
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0, take_profit=2070.50000)
        self.adapter.place_order(order)

        body = self.conn.post.call_args[1]["json"]
        assert body["order"]["takeProfitOnFill"] == {"price": "2070.50000"}

    def test_both_sl_and_tp_included(self) -> None:
        self.conn.post.return_value = _make_fill_response()
        order = Order(
            symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0,
            stop_loss=2040.00000, take_profit=2070.00000,
        )
        self.adapter.place_order(order)

        body = self.conn.post.call_args[1]["json"]
        assert "stopLossOnFill" in body["order"]
        assert "takeProfitOnFill" in body["order"]

    def test_no_sl_tp_when_none(self) -> None:
        self.conn.post.return_value = _make_fill_response()
        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0)
        self.adapter.place_order(order)

        body = self.conn.post.call_args[1]["json"]
        assert "stopLossOnFill" not in body["order"]
        assert "takeProfitOnFill" not in body["order"]


class TestCancelOrder:
    def test_cancel_returns_false(self) -> None:
        conn = MagicMock(spec=OANDAConnection)
        adapter = OANDABrokerAdapter(connection=conn, account_id="101-001-123")
        assert adapter.cancel_order(uuid4()) is False


class TestGetPositions:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_long_position(self) -> None:
        self.conn.get.return_value = _make_position_response(
            long_units="10", long_avg="2050.00", long_pnl="150.00"
        )
        pos = self.adapter.get_positions("XAU_USD")

        assert pos is not None
        assert pos.symbol == "XAU_USD"
        assert pos.quantity == 10.0
        assert pos.average_cost == 2050.0
        assert pos.unrealized_pnl == 150.0

    def test_short_position(self) -> None:
        self.conn.get.return_value = _make_position_response(
            short_units="-5", short_avg="2060.00", short_pnl="75.00"
        )
        pos = self.adapter.get_positions("XAU_USD")

        assert pos is not None
        assert pos.quantity == -5.0
        assert pos.average_cost == 2060.0
        assert pos.unrealized_pnl == 75.0

    def test_flat_returns_none(self) -> None:
        self.conn.get.return_value = _make_position_response(long_units="0", short_units="0")
        pos = self.adapter.get_positions("XAU_USD")
        assert pos is None

    def test_realized_pnl_mapped(self) -> None:
        self.conn.get.return_value = _make_position_response(
            long_units="5", long_avg="2050.00", realized_pnl="-200.00"
        )
        pos = self.adapter.get_positions("XAU_USD")
        assert pos is not None
        assert pos.realized_pnl == -200.0

    def test_calls_correct_endpoint(self) -> None:
        self.conn.get.return_value = _make_position_response()
        self.adapter.get_positions("XAU_USD")
        self.conn.get.assert_called_once_with("/v3/accounts/101-001-123/positions/XAU_USD")

    def test_404_returns_none(self) -> None:
        self.conn.get.side_effect = OANDAAPIError(404, "No such position")
        result = self.adapter.get_positions("XAU_USD")
        assert result is None

    def test_non_404_error_propagates(self) -> None:
        self.conn.get.side_effect = OANDAAPIError(500, "Server error")
        try:
            self.adapter.get_positions("XAU_USD")
            assert False, "Should have raised"  # noqa: B011
        except OANDAAPIError:
            pass


class TestPlaceOrderBrokerTradeId:
    """Test that place_order captures the broker trade ID."""

    def test_broker_trade_id_from_fill_response(self) -> None:
        conn = MagicMock(spec=OANDAConnection)
        conn.post.return_value = _make_fill_response(price="2055.00")
        adapter = OANDABrokerAdapter(connection=conn, account_id="101-001-123")

        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=1.0)
        trade = adapter.place_order(order)

        assert trade.broker_trade_id == "6368"

    def test_broker_trade_id_empty_when_no_trade_opened(self) -> None:
        conn = MagicMock(spec=OANDAConnection)
        # Response without tradeOpened (e.g., trade reduced existing position)
        conn.post.return_value = {
            "orderFillTransaction": {
                "id": "6370",
                "type": "ORDER_FILL",
                "instrument": "XAU_USD",
                "units": "5",
                "price": "2055.00",
                "accountBalance": "100000.0",
            }
        }
        adapter = OANDABrokerAdapter(connection=conn, account_id="101-001-123")

        order = Order(symbol="XAU_USD", side=OrderSide.BUY, quantity=5.0)
        trade = adapter.place_order(order)

        assert trade.broker_trade_id == ""


class TestGetOpenTrades:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_returns_open_trades_for_symbol(self) -> None:
        self.conn.get.return_value = {
            "trades": [
                {"id": "100", "instrument": "XAU_USD", "currentUnits": "5", "price": "2050.0"},
                {"id": "101", "instrument": "XAU_USD", "currentUnits": "-3", "price": "2060.0"},
                {"id": "102", "instrument": "EUR_USD", "currentUnits": "1000", "price": "1.08"},
            ]
        }
        trades = self.adapter.get_open_trades("XAU_USD")

        assert len(trades) == 2
        assert trades[0].broker_trade_id == "100"
        assert trades[0].side == OrderSide.BUY
        assert trades[0].quantity == 5.0
        assert trades[1].broker_trade_id == "101"
        assert trades[1].side == OrderSide.SELL
        assert trades[1].quantity == 3.0

    def test_empty_when_no_trades(self) -> None:
        self.conn.get.return_value = {"trades": []}
        trades = self.adapter.get_open_trades("XAU_USD")
        assert trades == []

    def test_calls_correct_endpoint(self) -> None:
        self.conn.get.return_value = {"trades": []}
        self.adapter.get_open_trades("XAU_USD")
        self.conn.get.assert_called_once_with("/v3/accounts/101-001-123/openTrades")


class TestGetPendingOrders:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_includes_both_limit_and_stop_orders(self) -> None:
        """Grid strategies rest LIMIT and STOP entries — both must be reported.

        Regression: excluding STOP made resting stops look 'cancelled' to the
        engine's fill detection, causing a place/cancel churn loop in production.
        """
        self.conn.get.return_value = {
            "orders": [
                {"id": "201", "instrument": "XAU_USD", "type": "LIMIT",
                 "units": "2", "price": "4200.0"},
                {"id": "202", "instrument": "XAU_USD", "type": "STOP",
                 "units": "2", "price": "4232.56"},
                {"id": "203", "instrument": "XAU_USD", "type": "STOP",
                 "units": "-2", "price": "4180.0"},
            ]
        }
        pending = self.adapter.get_pending_orders("XAU_USD")

        assert {p.broker_order_id for p in pending} == {"201", "202", "203"}
        stop_buy = next(p for p in pending if p.broker_order_id == "202")
        assert stop_buy.side == OrderSide.BUY
        assert stop_buy.limit_price == 4232.56
        stop_sell = next(p for p in pending if p.broker_order_id == "203")
        assert stop_sell.side == OrderSide.SELL

    def test_excludes_other_order_types_and_instruments(self) -> None:
        self.conn.get.return_value = {
            "orders": [
                {"id": "301", "instrument": "XAU_USD", "type": "TAKE_PROFIT",
                 "units": "2", "price": "4300.0"},
                {"id": "302", "instrument": "EUR_USD", "type": "LIMIT",
                 "units": "1000", "price": "1.08"},
                {"id": "303", "instrument": "XAU_USD", "type": "LIMIT",
                 "units": "2", "price": "4200.0"},
            ]
        }
        pending = self.adapter.get_pending_orders("XAU_USD")
        assert {p.broker_order_id for p in pending} == {"303"}

    def test_calls_correct_endpoint(self) -> None:
        self.conn.get.return_value = {"orders": []}
        self.adapter.get_pending_orders("XAU_USD")
        self.conn.get.assert_called_once_with("/v3/accounts/101-001-123/pendingOrders")


class TestGetClosedTradeDetails:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDABrokerAdapter(connection=self.conn, account_id="101-001-123")

    def test_returns_details_for_closed_trade(self) -> None:
        self.conn.get.return_value = {
            "trade": {
                "id": "100",
                "state": "CLOSED",
                "closeReason": "TAKE_PROFIT_ORDER",
                "averageClosePrice": "2080.50",
                "realizedPL": "150.25",
            }
        }
        details = self.adapter.get_closed_trade_details("100")

        assert details is not None
        assert details.broker_trade_id == "100"
        assert details.close_price == 2080.50
        assert details.realized_pnl == 150.25
        assert details.close_reason == "TAKE_PROFIT"

    def test_stop_loss_reason(self) -> None:
        self.conn.get.return_value = {
            "trade": {
                "id": "101",
                "state": "CLOSED",
                "closeReason": "STOP_LOSS_ORDER",
                "averageClosePrice": "2020.00",
                "realizedPL": "-90.00",
            }
        }
        details = self.adapter.get_closed_trade_details("101")

        assert details is not None
        assert details.close_reason == "STOP_LOSS"
        assert details.realized_pnl == -90.0

    def test_returns_none_for_open_trade(self) -> None:
        self.conn.get.return_value = {
            "trade": {
                "id": "102",
                "state": "OPEN",
                "currentUnits": "5",
                "price": "2050.00",
            }
        }
        details = self.adapter.get_closed_trade_details("102")
        assert details is None

    def test_returns_none_when_no_trade(self) -> None:
        self.conn.get.return_value = {}
        details = self.adapter.get_closed_trade_details("999")
        assert details is None
