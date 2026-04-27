"""Tests for the Risk Engine — every rule independently verified."""

from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import Position, Signal, Trade
from aurex_trade.domain.risk.engine import RiskEngine


def _signal(symbol: str = "GLD") -> Signal:
    return Signal(symbol=symbol, signal_type=SignalType.LONG, strategy_name="test")


def _position(quantity: float = 0.0, unrealized_pnl: float = 0.0) -> Position:
    return Position(symbol="GLD", quantity=quantity, unrealized_pnl=unrealized_pnl)


def _trade(side: OrderSide = OrderSide.BUY, quantity: float = 1.0, price: float = 100.0) -> Trade:
    return Trade(
        symbol="GLD",
        side=side,
        quantity=quantity,
        price=price,
        commission=0.0,
    )


def _engine(
    max_position_size: int = 10,
    max_daily_loss: float = 500.0,
    max_trades_per_day: int = 10,
    kill_switch: bool = False,
) -> RiskEngine:
    return RiskEngine(
        max_position_size=max_position_size,
        max_daily_loss=max_daily_loss,
        max_trades_per_day=max_trades_per_day,
        kill_switch=kill_switch,
    )


class TestApproval:
    def test_approved_when_all_checks_pass(self) -> None:
        engine = _engine()
        result = engine.evaluate(_signal(), None, [])
        assert result.action == RiskAction.APPROVED
        assert result.reason == "All risk checks passed"

    def test_approved_with_existing_position_under_limit(self) -> None:
        engine = _engine(max_position_size=10)
        result = engine.evaluate(_signal(), _position(quantity=5.0), [])
        assert result.action == RiskAction.APPROVED

    def test_signal_id_is_preserved(self) -> None:
        signal = _signal()
        result = _engine().evaluate(signal, None, [])
        assert result.signal_id == signal.id


class TestKillSwitch:
    def test_kill_switch_rejects_everything(self) -> None:
        engine = _engine(kill_switch=True)
        result = engine.evaluate(_signal(), None, [])
        assert result.action == RiskAction.KILL_SWITCH
        assert "Kill switch" in result.reason

    def test_kill_switch_takes_priority_over_all_other_rules(self) -> None:
        """Even with valid position and no trades, kill switch still rejects."""
        engine = _engine(kill_switch=True, max_position_size=100, max_trades_per_day=100)
        result = engine.evaluate(_signal(), _position(quantity=0.0), [])
        assert result.action == RiskAction.KILL_SWITCH


class TestMaxPositionSize:
    def test_rejected_when_at_max_position(self) -> None:
        engine = _engine(max_position_size=10)
        result = engine.evaluate(_signal(), _position(quantity=10.0), [])
        assert result.action == RiskAction.REJECTED
        assert "Position size" in result.reason

    def test_rejected_when_over_max_position(self) -> None:
        engine = _engine(max_position_size=10)
        result = engine.evaluate(_signal(), _position(quantity=15.0), [])
        assert result.action == RiskAction.REJECTED

    def test_rejected_for_short_position_at_max(self) -> None:
        """Negative quantity (short position) should also be checked via abs()."""
        engine = _engine(max_position_size=10)
        result = engine.evaluate(_signal(), _position(quantity=-10.0), [])
        assert result.action == RiskAction.REJECTED

    def test_approved_when_below_max(self) -> None:
        engine = _engine(max_position_size=10)
        result = engine.evaluate(_signal(), _position(quantity=9.0), [])
        assert result.action == RiskAction.APPROVED


class TestMaxDailyLoss:
    def test_rejected_when_unrealized_loss_exceeds_limit(self) -> None:
        engine = _engine(max_daily_loss=500.0)
        result = engine.evaluate(_signal(), _position(unrealized_pnl=-500.0), [])
        assert result.action == RiskAction.REJECTED
        assert "Daily P&L" in result.reason

    def test_rejected_when_unrealized_loss_over_limit(self) -> None:
        engine = _engine(max_daily_loss=500.0)
        result = engine.evaluate(_signal(), _position(unrealized_pnl=-600.0), [])
        assert result.action == RiskAction.REJECTED

    def test_approved_when_loss_below_limit(self) -> None:
        engine = _engine(max_daily_loss=500.0)
        result = engine.evaluate(_signal(), _position(unrealized_pnl=-200.0), [])
        assert result.action == RiskAction.APPROVED

    def test_no_position_has_zero_pnl(self) -> None:
        engine = _engine(max_daily_loss=500.0)
        result = engine.evaluate(_signal(), None, [])
        assert result.action == RiskAction.APPROVED


class TestTradeFrequency:
    def test_rejected_when_at_max_trades(self) -> None:
        engine = _engine(max_trades_per_day=3)
        trades = [_trade() for _ in range(3)]
        result = engine.evaluate(_signal(), None, trades)
        assert result.action == RiskAction.REJECTED
        assert "3 trades today" in result.reason

    def test_rejected_when_over_max_trades(self) -> None:
        engine = _engine(max_trades_per_day=3)
        trades = [_trade() for _ in range(5)]
        result = engine.evaluate(_signal(), None, trades)
        assert result.action == RiskAction.REJECTED

    def test_approved_when_below_max_trades(self) -> None:
        engine = _engine(max_trades_per_day=3)
        trades = [_trade() for _ in range(2)]
        result = engine.evaluate(_signal(), None, trades)
        assert result.action == RiskAction.APPROVED


class TestRulePriority:
    """Kill switch should take priority, then position, then daily loss, then frequency."""

    def test_kill_switch_over_position_size(self) -> None:
        engine = _engine(kill_switch=True, max_position_size=1)
        result = engine.evaluate(_signal(), _position(quantity=100.0), [])
        assert result.action == RiskAction.KILL_SWITCH

    def test_position_size_over_daily_loss(self) -> None:
        engine = _engine(max_position_size=5, max_daily_loss=10.0)
        result = engine.evaluate(
            _signal(),
            _position(quantity=10.0, unrealized_pnl=-1000.0),
            [],
        )
        assert result.action == RiskAction.REJECTED
        assert "Position size" in result.reason

    def test_daily_loss_over_trade_frequency(self) -> None:
        engine = _engine(max_daily_loss=10.0, max_trades_per_day=1)
        trades = [_trade() for _ in range(5)]
        result = engine.evaluate(
            _signal(),
            _position(unrealized_pnl=-1000.0),
            trades,
        )
        assert result.action == RiskAction.REJECTED
        assert "Daily P&L" in result.reason
