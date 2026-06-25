"""Realized P&L derived from account-balance deltas (not per-trade history).

This guards the production fix: OANDA's per-trade history endpoints 504 on
long-lived accounts, so the engine must derive realized P&L from balance
snapshots (balance changes only when P&L is realized) and push it to the
strategy. The previous code recorded every closure as $0, so the session
profit-target fired on unrealized P&L alone while realized losses were invisible.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.ciby_sliding_grid import CibySlidingGridStrategy
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _build_engine(
    start_balance: float,
) -> tuple[TradingEngine, MagicMock, CibySlidingGridStrategy]:
    broker = MagicMock()
    broker.equity = start_balance
    broker.get_positions.return_value = None  # flat — get_metrics reads this
    broker.get_account_summary.return_value = {
        "balance": start_balance,
        "unrealized_pnl": 0.0,
        "open_position_count": 0,
    }
    strategy = CibySlidingGridStrategy(
        grid_spacing=10.0,
        anchor_gap=15.0,
        session_profit_target=30.0,
        session_loss_limit=50.0,
        daily_loss_limit=200.0,
    )
    engine = TradingEngine(
        strategy=strategy,
        risk_engine=RiskEngine(
            max_position_size=100, max_daily_loss=5000.0, max_trades_per_day=200
        ),
        broker=broker,
        market_data=broker,
        repository=InMemoryRepository(),
        symbol="XAU_USD",
        interval_seconds=0,
        bar_count=10,
        user_id=_TEST_USER_ID,
    )
    return engine, broker, strategy


def _seed_balance_anchors(engine: TradingEngine, balance: float) -> None:
    """Mimic run()'s start-up seeding without launching the loop."""
    engine._run_start_balance = balance
    engine._session_start_balance = balance
    engine._day_start_balance = balance
    engine._balance_day = datetime.now(UTC).strftime("%Y-%m-%d")
    engine._last_balance = balance


class TestBalanceDeltaPush:
    def test_balance_drop_becomes_realized_loss(self) -> None:
        engine, _broker, strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        # Balance fell $40 (a realized stop-loss the bot can't see per-trade).
        engine._push_realized_pnl(99_960.0)
        assert strategy._session_realized_pnl == -40.0
        assert strategy._daily_realized_pnl == -40.0
        assert strategy._realized_authoritative is True

    def test_push_sets_not_accumulates(self) -> None:
        engine, _broker, strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        engine._push_realized_pnl(99_990.0)  # -10
        engine._push_realized_pnl(99_970.0)  # cumulative -30 from the SAME anchor
        assert strategy._session_realized_pnl == -30.0

    def test_realized_loss_reaches_session_gauge(self) -> None:
        # The prod bug: realized balance losses must reach the session gauge so
        # the loss limit can act on them (the trigger itself fires in generate()).
        engine, _broker, strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        engine._push_realized_pnl(99_940.0)  # -60 < -50 session loss limit
        # Below the -50 loss limit purely from realized P&L. The strategy acts on
        # its internal tally; the UI reads the engine's balance-delta truth.
        # (Display state no longer carries P&L figures — issue #74.)
        assert strategy._session_realized_pnl == -60.0

    def test_day_rollover_reanchors_daily_baseline(self) -> None:
        engine, _broker, strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        engine._push_realized_pnl(99_900.0)  # daily -100 on 2025-05-01
        assert strategy._daily_realized_pnl == -100.0
        # Simulate a new UTC day by moving the recorded day backwards.
        engine._balance_day = "1999-01-01"
        engine._push_realized_pnl(99_900.0)
        # Daily baseline re-anchored to current balance → daily realized resets.
        assert strategy._daily_realized_pnl == 0.0

    def test_no_account_summary_skips_push(self) -> None:
        # Brokers without get_account_summary (paper/backtest) must not break.
        engine, broker, _strategy = _build_engine(100_000.0)
        del broker.get_account_summary
        # Anchors never seeded; calling the strategy push path is a no-op guard.
        assert engine._session_start_balance is None


class TestAuthoritativeFinancials:
    """get_financials()/get_metrics() expose balance-delta truth for the UI.

    These are the single authoritative source for headline P&L (issue #74); the
    strategy display state no longer carries any financial figures.
    """

    def test_metrics_report_balance_delta_pnl(self) -> None:
        engine, broker, _strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        # Session started at 99_950 (already -50 on the day before this session),
        # day started at 99_900. Current balance 99_930.
        engine._session_start_balance = 99_950.0
        engine._day_start_balance = 99_900.0
        broker.get_account_summary.return_value = {
            "balance": 99_930.0,
            "unrealized_pnl": 12.0,
            "open_position_count": 1,
        }
        engine._last_balance = 99_930.0

        metrics = engine.get_metrics()
        assert metrics["realized_pnl"] == -70.0  # run delta: 99_930 - 100_000
        assert metrics["session_realized_pnl"] == -20.0  # 99_930 - 99_950
        assert metrics["daily_pnl"] == 30.0  # 99_930 - 99_900

    def test_financials_combine_realized_and_floating(self) -> None:
        engine, broker, _strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        broker.get_account_summary.return_value = {
            "balance": 99_960.0,  # -40 realized this session
            "unrealized_pnl": 15.0,  # floating
            "open_position_count": 1,
        }
        engine._last_balance = 99_960.0

        fin = engine.get_financials()
        # Net session P&L = banked (-40) + floating (+15) = -25.
        assert fin["session_realized_pnl"] == -40.0
        assert fin["session_unrealized_pnl"] == 15.0
        assert fin["session_pnl"] == -25.0
        # Configured limits come from the strategy.
        assert fin["session_loss_limit"] == 50.0
        assert fin["session_profit_target"] == 30.0
        assert fin["daily_loss_limit"] == 200.0

    def test_financials_omit_missing_limits(self) -> None:
        # A strategy without a profit target reports None for it (the UI then
        # renders a one-sided gauge). Simulate by clearing the attribute.
        engine, broker, strategy = _build_engine(100_000.0)
        _seed_balance_anchors(engine, 100_000.0)
        del strategy._session_profit_target
        broker.get_account_summary.return_value = {
            "balance": 100_000.0,
            "unrealized_pnl": 0.0,
            "open_position_count": 0,
        }
        fin = engine.get_financials()
        assert fin["session_profit_target"] is None
        assert fin["session_loss_limit"] == 50.0
