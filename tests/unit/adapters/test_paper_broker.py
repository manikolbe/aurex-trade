"""Tests for the Paper Broker adapter."""

from aurex_trade.adapters.paper.broker import PaperBrokerAdapter
from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import Order


class TestMarketData:
    def setup_method(self) -> None:
        self.adapter = PaperBrokerAdapter(base_price=180.0, seed=42)

    def test_get_latest_bars_returns_requested_count(self) -> None:
        bars = self.adapter.get_latest_bars("GLD", 10)
        assert len(bars) == 10

    def test_bars_have_correct_symbol(self) -> None:
        bars = self.adapter.get_latest_bars("GLD", 5)
        assert all(b.symbol == "GLD" for b in bars)

    def test_bars_have_positive_prices(self) -> None:
        bars = self.adapter.get_latest_bars("GLD", 50)
        for bar in bars:
            assert bar.open > 0
            assert bar.high > 0
            assert bar.low > 0
            assert bar.close > 0

    def test_high_gte_low(self) -> None:
        bars = self.adapter.get_latest_bars("GLD", 50)
        for bar in bars:
            assert bar.high >= bar.low

    def test_bars_are_consistent_across_calls(self) -> None:
        """Calling get_latest_bars multiple times should extend, not regenerate."""
        bars5 = self.adapter.get_latest_bars("GLD", 5)
        bars10 = self.adapter.get_latest_bars("GLD", 10)
        # First 5 bars of the 10 should match the original 5
        assert [b.close for b in bars10[:5]] == [b.close for b in bars5]

    def test_deterministic_with_seed(self) -> None:
        a = PaperBrokerAdapter(base_price=180.0, seed=42)
        b = PaperBrokerAdapter(base_price=180.0, seed=42)
        bars_a = a.get_latest_bars("GLD", 20)
        bars_b = b.get_latest_bars("GLD", 20)
        assert [b.close for b in bars_a] == [b.close for b in bars_b]


class TestPlaceOrder:
    def setup_method(self) -> None:
        self.adapter = PaperBrokerAdapter(base_price=180.0, seed=42)

    def test_buy_order_returns_trade(self) -> None:
        order = Order(symbol="GLD", side=OrderSide.BUY, quantity=5.0)
        trade = self.adapter.place_order(order)
        assert trade.order_id == order.id
        assert trade.symbol == "GLD"
        assert trade.side == OrderSide.BUY
        assert trade.quantity == 5.0
        assert trade.price > 0

    def test_buy_creates_position(self) -> None:
        order = Order(symbol="GLD", side=OrderSide.BUY, quantity=5.0)
        self.adapter.place_order(order)
        pos = self.adapter.get_positions("GLD")
        assert pos is not None
        assert pos.quantity == 5.0

    def test_sell_reduces_position(self) -> None:
        buy = Order(symbol="GLD", side=OrderSide.BUY, quantity=10.0)
        self.adapter.place_order(buy)
        sell = Order(symbol="GLD", side=OrderSide.SELL, quantity=3.0)
        self.adapter.place_order(sell)
        pos = self.adapter.get_positions("GLD")
        assert pos is not None
        assert pos.quantity == 7.0

    def test_sell_all_zeros_position(self) -> None:
        buy = Order(symbol="GLD", side=OrderSide.BUY, quantity=5.0)
        self.adapter.place_order(buy)
        sell = Order(symbol="GLD", side=OrderSide.SELL, quantity=5.0)
        self.adapter.place_order(sell)
        pos = self.adapter.get_positions("GLD")
        assert pos is not None
        assert pos.quantity == 0.0


class TestCancelOrder:
    def test_cancel_returns_false(self) -> None:
        """Paper broker fills immediately — nothing to cancel."""
        from uuid import uuid4

        adapter = PaperBrokerAdapter()
        assert adapter.cancel_order(uuid4()) is False


class TestGetPositions:
    def test_returns_none_when_no_position(self) -> None:
        adapter = PaperBrokerAdapter()
        assert adapter.get_positions("GLD") is None

    def test_different_symbols_independent(self) -> None:
        adapter = PaperBrokerAdapter(seed=42)
        adapter.place_order(Order(symbol="GLD", side=OrderSide.BUY, quantity=5.0))
        adapter.place_order(Order(symbol="SPY", side=OrderSide.BUY, quantity=3.0))
        gld = adapter.get_positions("GLD")
        spy = adapter.get_positions("SPY")
        assert gld is not None and gld.quantity == 5.0
        assert spy is not None and spy.quantity == 3.0
