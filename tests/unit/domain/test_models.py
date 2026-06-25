"""Tests for domain models — verifying immutability, defaults, and construction."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from aurex_trade.domain.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    RiskAction,
    SignalType,
    TradingMode,
)
from aurex_trade.domain.models import (
    BarData,
    Order,
    Position,
    RiskDecision,
    Signal,
    Trade,
)


class TestBarData:
    def test_creation(self) -> None:
        bar = BarData(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            open=180.0,
            high=181.0,
            low=179.0,
            close=180.5,
            volume=1000.0,
            symbol="GLD",
        )
        assert bar.symbol == "GLD"
        assert bar.close == 180.5

    def test_immutability(self) -> None:
        bar = BarData(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            open=180.0,
            high=181.0,
            low=179.0,
            close=180.5,
            volume=1000.0,
            symbol="GLD",
        )
        with pytest.raises(AttributeError):
            bar.close = 999.0  # type: ignore[misc]


class TestSignal:
    def test_defaults(self) -> None:
        signal = Signal()
        assert isinstance(signal.id, UUID)
        assert signal.signal_type == SignalType.FLAT
        assert signal.strength == 0.0
        assert signal.metadata == {}

    def test_with_values(self) -> None:
        signal = Signal(
            symbol="GLD",
            signal_type=SignalType.LONG,
            strategy_name="ciby_sliding_grid",
            strength=0.8,
        )
        assert signal.symbol == "GLD"
        assert signal.signal_type == SignalType.LONG
        assert signal.strategy_name == "ciby_sliding_grid"

    def test_immutability(self) -> None:
        signal = Signal()
        with pytest.raises(AttributeError):
            signal.symbol = "SPY"  # type: ignore[misc]

    def test_unique_ids(self) -> None:
        s1 = Signal()
        s2 = Signal()
        assert s1.id != s2.id


class TestRiskDecision:
    def test_defaults(self) -> None:
        decision = RiskDecision()
        assert decision.action == RiskAction.REJECTED
        assert decision.reason == ""

    def test_approved(self) -> None:
        decision = RiskDecision(action=RiskAction.APPROVED, reason="all checks passed")
        assert decision.action == RiskAction.APPROVED


class TestOrder:
    def test_defaults_to_market_order(self) -> None:
        order = Order()
        assert order.status == OrderStatus.PENDING
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.MARKET
        assert order.limit_price is None
        assert order.quantity == 0.0

    def test_sell_order(self) -> None:
        order = Order(
            symbol="GLD",
            side=OrderSide.SELL,
            quantity=5.0,
        )
        assert order.side == OrderSide.SELL
        assert order.quantity == 5.0

    def test_limit_order(self) -> None:
        order = Order(
            symbol="GLD",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=10.0,
            limit_price=180.0,
        )
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == 180.0


class TestTrade:
    def test_creation(self) -> None:
        trade = Trade(
            symbol="GLD",
            side=OrderSide.BUY,
            quantity=10.0,
            price=185.50,
            commission=1.0,
        )
        assert trade.price == 185.50
        assert trade.commission == 1.0


class TestPosition:
    def test_defaults(self) -> None:
        pos = Position()
        assert pos.quantity == 0.0
        assert pos.unrealized_pnl == 0.0

    def test_with_values(self) -> None:
        pos = Position(
            symbol="GLD",
            quantity=10.0,
            average_cost=180.0,
            market_value=1850.0,
            unrealized_pnl=50.0,
        )
        assert pos.market_value == 1850.0


class TestEnums:
    def test_trading_mode_values(self) -> None:
        assert TradingMode.LOCAL == "local"
        assert TradingMode.PAPER == "paper"
        assert TradingMode.LIVE == "live"

    def test_order_side_values(self) -> None:
        assert OrderSide.BUY == "buy"
        assert OrderSide.SELL == "sell"

    def test_signal_type_members(self) -> None:
        assert SignalType.LONG in SignalType
        assert SignalType.SHORT in SignalType
        assert SignalType.FLAT in SignalType

    def test_order_type_values(self) -> None:
        assert OrderType.MARKET == "market"
        assert OrderType.LIMIT == "limit"

    def test_risk_action_values(self) -> None:
        assert RiskAction.APPROVED == "approved"
        assert RiskAction.REJECTED == "rejected"
        assert RiskAction.KILL_SWITCH == "kill_switch"
