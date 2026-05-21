"""Tests for SimulatedBrokerAdapter — fill prices, position tracking, determinism."""

from datetime import UTC, datetime

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import BarData, Order


def _bar(close: float = 100.0) -> BarData:
    return BarData(
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=close,
        volume=1000.0,
        symbol="TEST",
    )


def _order(side: OrderSide = OrderSide.BUY, quantity: float = 1.0) -> Order:
    return Order(symbol="TEST", side=side, quantity=quantity)


class TestFillPrices:
    def test_buy_fills_above_mid(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=2.0, slippage=0.0, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))
        trade = broker.place_order(_order(OrderSide.BUY))
        # Buy fills at close + half_spread = 100 + 1 = 101
        assert trade.price >= 100.0

    def test_sell_fills_below_mid(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=2.0, slippage=0.0, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))
        # Open a long first
        broker.place_order(_order(OrderSide.BUY))
        trade = broker.place_order(_order(OrderSide.SELL))
        # Sell fills at close - half_spread = 100 - 1 = 99
        assert trade.price <= 100.0

    def test_zero_spread_and_slippage(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))
        trade = broker.place_order(_order(OrderSide.BUY))
        assert trade.price == 100.0


class TestPositionTracking:
    def test_opening_position(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))
        broker.place_order(_order(OrderSide.BUY, quantity=2.0))

        pos = broker.get_positions("TEST")
        assert pos is not None
        assert pos.quantity == 2.0
        assert pos.average_cost == 100.0

    def test_closing_position_realizes_pnl(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        # Buy at 100
        broker.set_current_bar(_bar(close=100.0))
        broker.place_order(_order(OrderSide.BUY, quantity=1.0))

        # Sell at 110
        broker.set_current_bar(_bar(close=110.0))
        broker.place_order(_order(OrderSide.SELL, quantity=1.0))

        pos = broker.get_positions("TEST")
        assert pos is not None
        assert pos.quantity == 0.0
        assert pos.realized_pnl == 10.0

    def test_equity_reflects_unrealized_pnl(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))
        broker.place_order(_order(OrderSide.BUY, quantity=1.0))

        # Price moves to 105
        broker.set_current_bar(_bar(close=105.0))
        assert broker.equity == 100_005.0

    def test_commission_deducted(self) -> None:
        broker = SimulatedBrokerAdapter(
            initial_capital=100_000.0, spread=0.0, slippage=0.0, commission_per_trade=5.0, seed=42
        )
        broker.set_current_bar(_bar(close=100.0))
        trade = broker.place_order(_order(OrderSide.BUY))
        assert trade.commission == 5.0
        assert broker.total_commission == 5.0


class TestDeterminism:
    def test_same_seed_same_fills(self) -> None:
        def run_trade(seed: int) -> float:
            broker = SimulatedBrokerAdapter(
                initial_capital=100_000.0, spread=1.0, slippage=0.5, seed=seed
            )
            broker.set_current_bar(_bar(close=100.0))
            return broker.place_order(_order(OrderSide.BUY)).price

        price1 = run_trade(42)
        price2 = run_trade(42)
        price3 = run_trade(99)

        assert price1 == price2
        assert price1 != price3
