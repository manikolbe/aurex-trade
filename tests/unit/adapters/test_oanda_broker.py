"""Tests for the OANDA broker adapter."""

from unittest.mock import MagicMock
from uuid import uuid4

from aurex_trade.adapters.oanda.broker import OANDABrokerAdapter
from aurex_trade.adapters.oanda.connection import OANDAAPIError, OANDAConnection
from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import Order


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

    def test_api_error_propagates(self) -> None:
        self.conn.get.side_effect = OANDAAPIError(404, "No such position")
        try:
            self.adapter.get_positions("XAU_USD")
            assert False, "Should have raised"  # noqa: B011
        except OANDAAPIError:
            pass
