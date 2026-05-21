"""Tests for the Risk Engine — every rule independently verified."""

from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import AccountState, Position, Signal, Trade
from aurex_trade.domain.risk.engine import RiskEngine


def _signal(symbol: str = "GLD", stop_loss: float | None = 95.0) -> Signal:
    return Signal(
        symbol=symbol,
        signal_type=SignalType.LONG,
        strategy_name="test",
        stop_loss=stop_loss,
    )


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
    require_stop_loss: bool = True,
    risk_per_trade: float = 0.02,
    max_drawdown_pct: float = 0.20,
    max_consecutive_losses: int = 5,
) -> RiskEngine:
    return RiskEngine(
        max_position_size=max_position_size,
        max_daily_loss=max_daily_loss,
        max_trades_per_day=max_trades_per_day,
        kill_switch=kill_switch,
        require_stop_loss=require_stop_loss,
        risk_per_trade=risk_per_trade,
        max_drawdown_pct=max_drawdown_pct,
        max_consecutive_losses=max_consecutive_losses,
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

    def test_opening_buy_does_not_trigger_loss_limit(self) -> None:
        """Regression: buying expensive assets must not count purchase cost as a loss."""
        engine = _engine(max_daily_loss=500.0)
        # Position opened at $4572 with tiny unrealized loss (spread)
        pos = Position(
            symbol="XAU_USD",
            quantity=1.0,
            average_cost=4572.36,
            market_value=4572.36,
            unrealized_pnl=-0.50,
            realized_pnl=0.0,
        )
        # The trade that opened this position
        trades = [_trade(side=OrderSide.BUY, quantity=1.0, price=4572.36)]
        result = engine.evaluate(_signal(), pos, trades)
        assert result.action == RiskAction.APPROVED

    def test_uses_realized_plus_unrealized_pnl(self) -> None:
        """Daily P&L combines realized (closed trades) and unrealized (open position)."""
        engine = _engine(max_daily_loss=500.0)
        pos = Position(
            symbol="GLD",
            quantity=1.0,
            unrealized_pnl=-200.0,
            realized_pnl=-350.0,
        )
        result = engine.evaluate(_signal(), pos, [])
        assert result.action == RiskAction.REJECTED
        assert "Daily P&L -550.00" in result.reason


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
    """Kill switch > stop-loss > drawdown > consecutive > position > daily loss > frequency."""

    def test_kill_switch_over_position_size(self) -> None:
        engine = _engine(kill_switch=True, max_position_size=1)
        result = engine.evaluate(_signal(), _position(quantity=100.0), [])
        assert result.action == RiskAction.KILL_SWITCH

    def test_stop_loss_over_drawdown(self) -> None:
        engine = _engine(require_stop_loss=True, max_drawdown_pct=0.01)
        account = AccountState(equity=80_000, peak_equity=100_000)
        result = engine.evaluate(_signal(stop_loss=None), None, [], account_state=account)
        assert "no stop-loss" in result.reason

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


class TestStopLossEnforcement:
    def test_rejected_when_no_stop_loss(self) -> None:
        engine = _engine(require_stop_loss=True)
        result = engine.evaluate(_signal(stop_loss=None), None, [])
        assert result.action == RiskAction.REJECTED
        assert "no stop-loss" in result.reason

    def test_approved_when_stop_loss_provided(self) -> None:
        engine = _engine(require_stop_loss=True)
        result = engine.evaluate(_signal(stop_loss=95.0), None, [])
        assert result.action == RiskAction.APPROVED

    def test_skipped_when_disabled(self) -> None:
        engine = _engine(require_stop_loss=False)
        result = engine.evaluate(_signal(stop_loss=None), None, [])
        assert result.action == RiskAction.APPROVED


class TestMaxDrawdownBreaker:
    def test_rejected_when_drawdown_exceeds_limit(self) -> None:
        engine = _engine(max_drawdown_pct=0.10)
        account = AccountState(equity=85_000, peak_equity=100_000)  # 15% drawdown
        result = engine.evaluate(_signal(), None, [], account_state=account)
        assert result.action == RiskAction.REJECTED
        assert "drawdown" in result.reason

    def test_rejected_at_exactly_the_limit(self) -> None:
        engine = _engine(max_drawdown_pct=0.20)
        account = AccountState(equity=80_000, peak_equity=100_000)  # exactly 20%
        result = engine.evaluate(_signal(), None, [], account_state=account)
        assert result.action == RiskAction.REJECTED

    def test_approved_when_drawdown_below_limit(self) -> None:
        engine = _engine(max_drawdown_pct=0.20)
        account = AccountState(equity=85_000, peak_equity=100_000)  # 15% drawdown
        result = engine.evaluate(_signal(), None, [], account_state=account)
        assert result.action == RiskAction.APPROVED

    def test_skipped_when_no_account_state(self) -> None:
        engine = _engine(max_drawdown_pct=0.01)
        result = engine.evaluate(_signal(), None, [])
        assert result.action == RiskAction.APPROVED

    def test_skipped_when_peak_is_zero(self) -> None:
        engine = _engine(max_drawdown_pct=0.10)
        account = AccountState(equity=0, peak_equity=0)
        result = engine.evaluate(_signal(), None, [], account_state=account)
        assert result.action == RiskAction.APPROVED


class TestConsecutiveLossPause:
    def test_rejected_after_n_consecutive_losses(self) -> None:
        engine = _engine(max_consecutive_losses=3)
        pnls = [-10.0, -5.0, -8.0]
        result = engine.evaluate(_signal(), None, [], recent_trade_pnls=pnls)
        assert result.action == RiskAction.REJECTED
        assert "Consecutive loss" in result.reason

    def test_approved_when_win_breaks_streak(self) -> None:
        engine = _engine(max_consecutive_losses=3)
        pnls = [-10.0, -5.0, 2.0]  # Last trade was a win
        result = engine.evaluate(_signal(), None, [], recent_trade_pnls=pnls)
        assert result.action == RiskAction.APPROVED

    def test_approved_when_fewer_trades_than_limit(self) -> None:
        engine = _engine(max_consecutive_losses=5)
        pnls = [-10.0, -5.0]  # Only 2 losses, need 5
        result = engine.evaluate(_signal(), None, [], recent_trade_pnls=pnls)
        assert result.action == RiskAction.APPROVED

    def test_skipped_when_no_pnls_provided(self) -> None:
        engine = _engine(max_consecutive_losses=1)
        result = engine.evaluate(_signal(), None, [])
        assert result.action == RiskAction.APPROVED


class TestPositionSizing:
    def test_basic_calculation(self) -> None:
        engine = _engine(risk_per_trade=0.01, max_position_size=1000)
        signal = _signal(stop_loss=95.0)
        account = AccountState(equity=100_000, peak_equity=100_000)
        # risk = 100_000 * 0.01 = 1000; distance = |100 - 95| = 5; units = 200
        size = engine.calculate_position_size(signal, account, entry_price=100.0)
        assert size == 200.0

    def test_capped_at_max_position_size(self) -> None:
        engine = _engine(risk_per_trade=0.02, max_position_size=10)
        signal = _signal(stop_loss=95.0)
        account = AccountState(equity=100_000, peak_equity=100_000)
        size = engine.calculate_position_size(signal, account, entry_price=100.0)
        assert size == 10.0  # Capped

    def test_zero_when_no_stop_loss(self) -> None:
        engine = _engine(risk_per_trade=0.02)
        signal = _signal(stop_loss=None)
        account = AccountState(equity=100_000, peak_equity=100_000)
        size = engine.calculate_position_size(signal, account, entry_price=100.0)
        assert size == 0.0

    def test_zero_when_stop_equals_entry(self) -> None:
        engine = _engine(risk_per_trade=0.02)
        signal = _signal(stop_loss=100.0)
        account = AccountState(equity=100_000, peak_equity=100_000)
        size = engine.calculate_position_size(signal, account, entry_price=100.0)
        assert size == 0.0
