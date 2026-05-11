"""Tests for the in-memory repository adapter."""

from datetime import UTC, datetime, timedelta

from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade

USER_A = "user-a"
USER_B = "user-b"


class TestSaveAndRetrieve:
    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_save_signal(self) -> None:
        signal = Signal(symbol="GLD", signal_type=SignalType.LONG, strategy_name="test")
        self.repo.save_signal(signal, user_id=USER_A)
        assert self.repo.signal_count == 1

    def test_save_decision(self) -> None:
        decision = RiskDecision(action=RiskAction.APPROVED, reason="ok")
        self.repo.save_decision(decision, user_id=USER_A)
        assert self.repo.decision_count == 1

    def test_save_trade(self) -> None:
        trade = Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0)
        self.repo.save_trade(trade, user_id=USER_A)
        assert self.repo.trade_count == 1

    def test_save_position_upserts_by_user_and_symbol(self) -> None:
        pos1 = Position(symbol="GLD", quantity=5.0)
        pos2 = Position(symbol="GLD", quantity=10.0)
        self.repo.save_position(pos1, user_id=USER_A)
        self.repo.save_position(pos2, user_id=USER_A)
        result = self.repo.get_current_position("GLD", user_id=USER_A)
        assert result is not None
        assert result.quantity == 10.0


class TestGetCurrentPosition:
    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_returns_none_when_no_position(self) -> None:
        assert self.repo.get_current_position("GLD", user_id=USER_A) is None

    def test_returns_position_for_correct_symbol(self) -> None:
        self.repo.save_position(Position(symbol="GLD", quantity=5.0), user_id=USER_A)
        self.repo.save_position(Position(symbol="SPY", quantity=3.0), user_id=USER_A)
        result = self.repo.get_current_position("GLD", user_id=USER_A)
        assert result is not None
        assert result.symbol == "GLD"
        assert result.quantity == 5.0


class TestGetTradesToday:
    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_returns_empty_when_no_trades(self) -> None:
        assert self.repo.get_trades_today("GLD", user_id=USER_A) == []

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
        self.repo.save_trade(today_trade, user_id=USER_A)
        self.repo.save_trade(yesterday_trade, user_id=USER_A)
        result = self.repo.get_trades_today("GLD", user_id=USER_A)
        assert len(result) == 1
        assert result[0].id == today_trade.id

    def test_filters_by_symbol(self) -> None:
        self.repo.save_trade(
            Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0),
            user_id=USER_A,
        )
        self.repo.save_trade(
            Trade(symbol="SPY", side=OrderSide.BUY, quantity=1.0, price=400.0),
            user_id=USER_A,
        )
        assert len(self.repo.get_trades_today("GLD", user_id=USER_A)) == 1
        assert len(self.repo.get_trades_today("SPY", user_id=USER_A)) == 1


class TestUserIsolation:
    """Verify that different users cannot see each other's data."""

    def setup_method(self) -> None:
        self.repo = InMemoryRepository()

    def test_positions_isolated_by_user(self) -> None:
        self.repo.save_position(Position(symbol="GLD", quantity=5.0), user_id=USER_A)
        self.repo.save_position(Position(symbol="GLD", quantity=99.0), user_id=USER_B)
        result_a = self.repo.get_current_position("GLD", user_id=USER_A)
        result_b = self.repo.get_current_position("GLD", user_id=USER_B)
        assert result_a is not None
        assert result_a.quantity == 5.0
        assert result_b is not None
        assert result_b.quantity == 99.0

    def test_trades_isolated_by_user(self) -> None:
        self.repo.save_trade(
            Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0),
            user_id=USER_A,
        )
        self.repo.save_trade(
            Trade(symbol="GLD", side=OrderSide.SELL, quantity=2.0, price=185.0),
            user_id=USER_B,
        )
        assert len(self.repo.get_trades_today("GLD", user_id=USER_A)) == 1
        assert len(self.repo.get_trades_today("GLD", user_id=USER_B)) == 1
        assert self.repo.get_trades_today("GLD", user_id=USER_A)[0].quantity == 1.0
        assert self.repo.get_trades_today("GLD", user_id=USER_B)[0].quantity == 2.0
