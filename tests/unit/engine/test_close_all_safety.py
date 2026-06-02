"""Unit tests for close-all safety mechanisms: circuit breaker, backoff, and caps.

Tests the fixes for the production incident where failed close-all triggered
infinite session restarts, stacking 86 trades at the same price.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from aurex_trade.domain.enums import OrderSide, SignalType
from aurex_trade.domain.models import OpenBrokerTrade, Signal
from aurex_trade.domain.strategy.ciby_hedged_grid import CibyHedgedGridStrategy
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _make_engine(
    broker: MagicMock | None = None,
    strategy: MagicMock | None = None,
) -> TradingEngine:
    """Build a TradingEngine with mocked dependencies."""
    if broker is None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.get_open_trades.return_value = []
        broker.get_positions.return_value = None
        broker.cancel_all_orders.return_value = 0
    if strategy is None:
        strategy = MagicMock()
        strategy.name = "test_strategy"

    repository = MagicMock()
    repository.get_trades_today.return_value = []
    risk_engine = MagicMock()
    risk_engine._enabled = False
    market_data = MagicMock()

    engine = TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=market_data,
        repository=repository,
        symbol="XAU_USD",
        interval_seconds=0,
        bar_count=10,
        user_id=_TEST_USER_ID,
    )
    return engine


def _open_trade(trade_id: str = "123") -> OpenBrokerTrade:
    return OpenBrokerTrade(
        broker_trade_id=trade_id,
        symbol="XAU_USD",
        side=OrderSide.BUY,
        quantity=10.0,
        open_price=4500.0,
    )


class TestCloseAllNotifyOnlyOnSuccess:
    """Fix #1: notify_close_all_complete only called when all closes succeed."""

    def test_successful_close_all_notifies_strategy(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.cancel_all_orders.return_value = 0
        # First call: returns trades to close. Second call (verification): empty.
        broker.get_open_trades.side_effect = [
            [_open_trade("1"), _open_trade("2")],
            [],  # verification check
        ]
        broker.close_trade.return_value = None

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        engine._close_all_trades("session_profit_target")

        strategy.notify_close_all_complete.assert_called_once()

    def test_failed_close_does_not_notify_strategy(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.cancel_all_orders.return_value = 0
        broker.get_open_trades.return_value = [_open_trade("1")]
        broker.close_trade.side_effect = RuntimeError("INSUFFICIENT_MARGIN")

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        engine._close_all_trades("session_profit_target")

        strategy.notify_close_all_complete.assert_not_called()

    def test_partial_failure_does_not_notify(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.cancel_all_orders.return_value = 0
        broker.get_open_trades.return_value = [_open_trade("1"), _open_trade("2")]
        # First close succeeds, second fails
        broker.close_trade.side_effect = [None, RuntimeError("locked")]

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        engine._close_all_trades("session_profit_target")

        strategy.notify_close_all_complete.assert_not_called()


class TestCircuitBreaker:
    """Fix #2: engine stops after max retries with exponential backoff."""

    def test_engine_stops_after_max_retries(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.cancel_all_orders.return_value = 0
        broker.get_open_trades.return_value = [_open_trade("1")]
        broker.close_trade.side_effect = RuntimeError("locked")

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)
        engine._running = True

        # Exhaust retries
        for _ in range(TradingEngine._MAX_CLOSE_ALL_RETRIES):
            engine._close_all_next_retry_at = None  # bypass backoff for test speed
            engine._close_all_trades("session_profit_target")

        assert engine._close_all_failed_count == TradingEngine._MAX_CLOSE_ALL_RETRIES

        # Next call triggers circuit breaker
        engine._close_all_trades("session_profit_target")
        assert engine._running is False

    def test_backoff_delays_retry(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.cancel_all_orders.return_value = 0
        broker.get_open_trades.return_value = [_open_trade("1")]
        broker.close_trade.side_effect = RuntimeError("locked")

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        # First failure sets backoff
        engine._close_all_trades("test")
        assert engine._close_all_next_retry_at is not None
        assert engine._close_all_next_retry_at > datetime.now(UTC)

        # While in backoff window, close_all returns immediately without retrying
        broker.close_trade.reset_mock()
        engine._close_all_trades("test")
        broker.close_trade.assert_not_called()

    def test_successful_close_resets_counter(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.cancel_all_orders.return_value = 0
        broker.get_open_trades.side_effect = [
            [_open_trade("1")],  # first: trades to close
            RuntimeError("fail"),  # this shouldn't happen, let's use proper sequence
        ]
        broker.close_trade.side_effect = RuntimeError("locked")

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        # Fail once
        engine._close_all_trades("test")
        assert engine._close_all_failed_count == 1

        # Now succeed
        broker.close_trade.side_effect = None
        broker.get_open_trades.side_effect = [
            [_open_trade("1")],  # trades to close
            [],  # verification
        ]
        engine._close_all_next_retry_at = None
        engine._close_all_trades("test")
        assert engine._close_all_failed_count == 0


class TestCloseAllInProgress:
    """Fix #3: strategy emits FLAT but doesn't restart while close-all retrying."""

    def test_flag_set_on_trigger(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            session_profit_target=100.0,
        )
        strategy._trigger_close_all("session_profit_target")
        assert strategy._close_all_in_progress is True

    def test_flag_cleared_on_notify_complete(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=15.0)
        strategy._close_all_in_progress = True
        strategy._close_reason = "test"
        strategy.notify_close_all_complete()
        assert strategy._close_all_in_progress is False

    def test_generate_emits_flat_without_retrigger(self) -> None:
        from aurex_trade.domain.models import BarData

        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            session_profit_target=100.0,
        )
        bar = BarData(
            timestamp=datetime(2026, 6, 2, tzinfo=UTC),
            open=4510.0,
            high=4515.0,
            low=4505.0,
            close=4510.0,
            volume=100.0,
            symbol="XAU_USD",
        )

        # Simulate: strategy triggered close-all, flag is set
        strategy._close_all_in_progress = True
        strategy._close_reason = "session_profit_target"
        strategy._current_date = "2026-06-02"

        # generate() should emit FLAT signal
        signal = strategy.generate([bar])
        assert signal is not None
        assert signal.signal_type == SignalType.FLAT
        assert signal.metadata["action"] == "close_all"

        # Session history should NOT grow (no re-trigger)
        initial_history_len = len(strategy._session_history)
        signal2 = strategy.generate([bar])
        assert signal2 is not None
        assert len(strategy._session_history) == initial_history_len

    def test_no_new_grid_placed_while_close_in_progress(self) -> None:
        from aurex_trade.domain.models import BarData

        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            session_profit_target=100.0,
        )
        bar = BarData(
            timestamp=datetime(2026, 6, 2, tzinfo=UTC),
            open=4510.0,
            high=4515.0,
            low=4505.0,
            close=4510.0,
            volume=100.0,
            symbol="XAU_USD",
        )

        # Set up as if session was active
        strategy._current_date = "2026-06-02"
        strategy._anchor_price = 4510.0
        strategy._close_all_in_progress = True
        strategy._close_reason = "session_profit_target"

        # Should return FLAT, not place new grid levels
        signal = strategy.generate([bar])
        assert signal is not None
        assert signal.signal_type == SignalType.FLAT
        # No signals queued for grid placement
        assert len(strategy._signal_queue) == 0


class TestMaxOpenTradesCap:
    """Fix #6: hard cap on open trades independent of risk engine."""

    def test_rejects_signal_when_at_cap(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.get_open_trades.return_value = [_open_trade(str(i)) for i in range(20)]
        broker.get_positions.return_value = None
        broker.cancel_all_orders.return_value = 0

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        signal = Signal(
            symbol="XAU_USD",
            signal_type=SignalType.LONG,
            strategy_name="test",
            strength=1.0,
        )

        # open_trade_count >= max (20 >= 20)
        engine._process_signal(signal, 4500.0, None, [], open_trade_count=20)

        # Should not reach risk engine or order placement
        broker.place_order.assert_not_called()

    def test_allows_signal_below_cap(self) -> None:
        broker = MagicMock()
        broker.equity = 100_000.0
        broker.get_open_trades.return_value = []
        broker.get_positions.return_value = None
        broker.cancel_all_orders.return_value = 0

        strategy = MagicMock()
        strategy.name = "test"
        engine = _make_engine(broker=broker, strategy=strategy)

        signal = Signal(
            symbol="XAU_USD",
            signal_type=SignalType.LONG,
            strategy_name="test",
            strength=1.0,
            metadata={"fixed_units": "10"},
        )

        # open_trade_count < max (5 < 20) — should proceed to risk check
        engine._process_signal(signal, 4500.0, None, [], open_trade_count=5)

        # Risk engine was called (signal wasn't rejected by cap)
        engine._risk_engine.evaluate.assert_called_once()
