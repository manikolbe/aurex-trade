"""Unit tests for the wrong-side stop-loss guard in the trading engine.

Regression: a stopped grid leg could be re-placed with its original stop on the
wrong side of the new entry (a short stopped BELOW its entry, a long ABOVE). The
broker (OANDA) rejects these as STOP_LOSS_ON_FILL_LOSS — but only after a round
trip, once per cycle, for hours. The guard rejects them in-process first.
"""

from unittest.mock import MagicMock

from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import Signal, Trade
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _fill(side: OrderSide, price: float) -> Trade:
    """A concrete Trade for the broker mock to return on a successful fill."""
    return Trade(
        symbol="XAU_USD",
        side=side,
        quantity=10.0,
        price=price,
        commission=0.0,
        broker_trade_id="999",
    )


def _make_engine(broker: MagicMock, strategy: MagicMock) -> TradingEngine:
    repository = MagicMock()
    repository.get_trades_today.return_value = []
    risk_engine = MagicMock()
    risk_engine._enabled = False
    # Approve everything — we are testing the guard, not the risk engine.
    risk_engine.evaluate.return_value = MagicMock(
        action=RiskAction.APPROVED, reason="ok"
    )
    return TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=MagicMock(),
        repository=repository,
        symbol="XAU_USD",
        interval_seconds=0,
        bar_count=10,
        user_id=_TEST_USER_ID,
    )


def _signal(stop_loss: float, *, side: SignalType, order_type: str = "MARKET") -> Signal:
    return Signal(
        symbol="XAU_USD",
        signal_type=side,
        strategy_name="ciby_sliding_grid",
        strength=1.0,
        stop_loss=stop_loss,
        metadata={
            "grid_level": "4100.00_short",
            "fixed_units": "10.0",
            "order_type": order_type,
        },
    )


def test_market_short_with_stop_below_entry_is_rejected() -> None:
    """A short stopped BELOW the market entry must never reach the broker."""
    broker = MagicMock()
    broker.equity = 100_000.0
    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    engine = _make_engine(broker, strategy)

    # Market price 4108, short, stop at 4100 (below entry) — invalid.
    sig = _signal(4100.0, side=SignalType.SHORT)
    engine._process_signal(sig, latest_close=4108.0, pos=None, trades_list=[])

    broker.place_order.assert_not_called()
    strategy.on_signal_rejected.assert_called_once_with("4100.00_short")


def test_valid_short_stop_above_entry_is_placed() -> None:
    """A short with its stop ABOVE the entry is valid and goes through."""
    broker = MagicMock()
    broker.equity = 100_000.0
    broker.get_positions.return_value = None
    broker.place_order.return_value = _fill(OrderSide.SELL, 4108.0)
    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    engine = _make_engine(broker, strategy)

    sig = _signal(4120.0, side=SignalType.SHORT)
    engine._process_signal(sig, latest_close=4108.0, pos=None, trades_list=[])

    broker.place_order.assert_called_once()


def test_valid_long_stop_below_entry_is_placed() -> None:
    """A long with its stop BELOW the entry is valid and goes through."""
    broker = MagicMock()
    broker.equity = 100_000.0
    broker.get_positions.return_value = None
    broker.place_order.return_value = _fill(OrderSide.BUY, 4108.0)
    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    engine = _make_engine(broker, strategy)

    sig = Signal(
        symbol="XAU_USD",
        signal_type=SignalType.LONG,
        strategy_name="ciby_sliding_grid",
        strength=1.0,
        stop_loss=4090.0,
        metadata={"grid_level": "4100.00_long", "fixed_units": "10.0", "order_type": "MARKET"},
    )
    engine._process_signal(sig, latest_close=4108.0, pos=None, trades_list=[])

    broker.place_order.assert_called_once()


def test_long_with_stop_above_entry_is_rejected() -> None:
    """A long stopped ABOVE its entry is invalid and rejected before the broker."""
    broker = MagicMock()
    broker.equity = 100_000.0
    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    engine = _make_engine(broker, strategy)

    sig = Signal(
        symbol="XAU_USD",
        signal_type=SignalType.LONG,
        strategy_name="ciby_sliding_grid",
        strength=1.0,
        stop_loss=4120.0,
        metadata={"grid_level": "4100.00_long", "fixed_units": "10.0", "order_type": "MARKET"},
    )
    engine._process_signal(sig, latest_close=4108.0, pos=None, trades_list=[])

    broker.place_order.assert_not_called()
    strategy.on_signal_rejected.assert_called_once_with("4100.00_long")
