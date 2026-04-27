"""Tests for the in-memory repository adapter."""

from datetime import UTC, datetime, timedelta

from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class TestSaveAndRetrieve:
    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_save_signal(self) -> None:
        signal = Signal(symbol="GLD", signal_type=SignalType.LONG, strategy_name="test")
        self.repo.save_signal(signal)
        assert len(self.repo._signals) == 1
        assert self.repo._signals[0].id == signal.id

    def test_save_decision(self) -> None:
        decision = RiskDecision(action=RiskAction.APPROVED, reason="ok")
        self.repo.save_decision(decision)
        assert len(self.repo._decisions) == 1

    def test_save_trade(self) -> None:
        trade = Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0)
        self.repo.save_trade(trade)
        assert len(self.repo._trades) == 1

    def test_save_position_upserts_by_symbol(self) -> None:
        pos1 = Position(symbol="GLD", quantity=5.0)
        pos2 = Position(symbol="GLD", quantity=10.0)
        self.repo.save_position(pos1)
        self.repo.save_position(pos2)
        result = self.repo.get_current_position("GLD")
        assert result is not None
        assert result.quantity == 10.0


class TestGetCurrentPosition:
    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_returns_none_when_no_position(self) -> None:
        assert self.repo.get_current_position("GLD") is None

    def test_returns_position_for_correct_symbol(self) -> None:
        self.repo.save_position(Position(symbol="GLD", quantity=5.0))
        self.repo.save_position(Position(symbol="SPY", quantity=3.0))
        result = self.repo.get_current_position("GLD")
        assert result is not None
        assert result.symbol == "GLD"
        assert result.quantity == 5.0


class TestGetTradesToday:
    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_returns_empty_when_no_trades(self) -> None:
        assert self.repo.get_trades_today("GLD") == []

    def test_returns_only_today_trades(self) -> None:
        today_trade = Trade(
            symbol="GLD",
            side=OrderSide.BUY,
            quantity=1.0,
            price=180.0,
            timestamp=datetime.now(UTC),
        )
        yesterday_trade = Trade(
            symbol="GLD",
            side=OrderSide.SELL,
            quantity=1.0,
            price=181.0,
            timestamp=datetime.now(UTC) - timedelta(days=1),
        )
        self.repo.save_trade(today_trade)
        self.repo.save_trade(yesterday_trade)
        result = self.repo.get_trades_today("GLD")
        assert len(result) == 1
        assert result[0].id == today_trade.id

    def test_filters_by_symbol(self) -> None:
        self.repo.save_trade(
            Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0)
        )
        self.repo.save_trade(
            Trade(symbol="SPY", side=OrderSide.BUY, quantity=1.0, price=400.0)
        )
        assert len(self.repo.get_trades_today("GLD")) == 1
        assert len(self.repo.get_trades_today("SPY")) == 1
