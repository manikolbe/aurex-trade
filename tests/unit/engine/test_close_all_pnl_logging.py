"""Unit tests: close-all emits per-trade realized P&L into the event-sourced ledger.

Regression: _close_all_trades closed each trade and cleared _grid_trade_map before
the normal closure-detection loop ran, so trade_closed_by_broker was never emitted
for close-all'd trades. Every profit-target / loss-limit restart banked P&L into the
account that was invisible to the ledger — so summing trade_closed_by_broker
undercounted the true realized result (e.g. a winning anchor leg harvested at the
profit target showed up nowhere). The close path now mirrors that event.

Per-trade realized P&L is parsed from close_trade()'s return value (the OANDA close
response carries it), NOT from a follow-up get_closed_trade_details history lookup
(that endpoint 504s on long-lived accounts).
"""

from unittest.mock import MagicMock

from aurex_trade.domain.enums import OrderSide
from aurex_trade.domain.models import ClosedTradeInfo, OpenBrokerTrade
from aurex_trade.engine.trading_engine import TradingEngine

_TEST_USER_ID = "test-user"


def _make_engine(broker: MagicMock, strategy: MagicMock) -> TradingEngine:
    repository = MagicMock()
    repository.get_trades_today.return_value = []
    risk_engine = MagicMock()
    risk_engine._enabled = False
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


def _open_trade(trade_id: str) -> OpenBrokerTrade:
    return OpenBrokerTrade(
        broker_trade_id=trade_id,
        symbol="XAU_USD",
        side=OrderSide.BUY,
        quantity=10.0,
        open_price=4500.0,
    )


def _closed(trade_id: str, pnl: float, price: float) -> ClosedTradeInfo:
    return ClosedTradeInfo(
        broker_trade_id=trade_id,
        close_price=price,
        realized_pnl=pnl,
        close_reason="MARKET_ORDER_TRADE_CLOSE",
    )


def _closed_events(log_mock: MagicMock) -> list[dict]:
    """Extract trade_closed_by_broker events from a mocked structlog logger."""
    events = []
    for call in log_mock.info.call_args_list:
        if call.args and call.args[0] == "trade_closed_by_broker":
            events.append(call.kwargs)
    return events


def test_close_all_logs_per_trade_realized_pnl() -> None:
    """Each close-all'd trade emits trade_closed_by_broker with its realized P&L."""
    broker = MagicMock()
    broker.equity = 100_000.0
    broker.cancel_all_orders.return_value = 0
    broker.get_open_trades.side_effect = [
        [_open_trade("7253"), _open_trade("7440")],
        [],  # verification check: all closed
    ]
    # Realized P&L now comes from close_trade()'s return (the close response).
    broker.close_trade.side_effect = lambda tid: {
        "7253": _closed("7253", 417.50, 4269.45),   # winning anchor short
        "7440": _closed("7440", -41.00, 4307.41),
    }[tid]

    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    strategy.get_display_state.return_value = {"session_pnl": 376.5}
    engine = _make_engine(broker, strategy)
    engine._grid_trade_map = {"4321.41_short": "7253", "4296.41_short": "7440"}
    engine._log = MagicMock()

    engine._close_all_trades("session_profit_target")

    events = _closed_events(engine._log)
    assert len(events) == 2
    by_id = {e["broker_trade_id"]: e for e in events}
    assert by_id["7253"]["realized_pnl"] == 417.50
    assert by_id["7253"]["grid_level"] == "4321.41_short"
    assert by_id["7253"]["close_reason"] == "session_profit_target"
    assert by_id["7440"]["realized_pnl"] == -41.00
    # The winning leg's P&L is now in the ledger (was previously invisible).
    assert sum(e["realized_pnl"] for e in events) == 376.50

    # Each liquidated leg is also a per-trade row on the UI's Realized P&L card,
    # tagged session_close with exact P&L — one row per trade, not a rollup.
    ledger = engine.get_realized_ledger()
    assert len(ledger) == 2
    rows = {r["broker_trade_id"]: r for r in ledger}
    assert rows["7253"]["kind"] == "session_close"
    assert rows["7253"]["basis"] == "exact"
    assert rows["7253"]["realized_pnl"] == 417.50
    assert rows["7253"]["grid_level"] == "4321.41_short"
    assert rows["7440"]["kind"] == "session_close"
    assert rows["7440"]["realized_pnl"] == -41.00


def test_close_all_pnl_feeds_consecutive_loss_tracking() -> None:
    """Realized P&L from close-all is appended to _trade_pnls (risk-engine input)."""
    broker = MagicMock()
    broker.equity = 100_000.0
    broker.cancel_all_orders.return_value = 0
    broker.get_open_trades.side_effect = [[_open_trade("1")], []]
    broker.close_trade.return_value = _closed("1", -240.4, 4257.08)

    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    strategy.get_display_state.return_value = {"session_pnl": -240.4}
    engine = _make_engine(broker, strategy)
    engine._grid_trade_map = {"4268.09_long": "1"}
    engine._log = MagicMock()

    engine._close_all_trades("session_loss_limit")

    assert -240.4 in engine._trade_pnls


def test_close_all_with_no_parsed_fill_still_completes() -> None:
    """If close_trade returns None (no fill parsed), close-all still completes."""
    broker = MagicMock()
    broker.equity = 100_000.0
    broker.cancel_all_orders.return_value = 0
    broker.get_open_trades.side_effect = [[_open_trade("1")], []]
    broker.close_trade.return_value = None  # no closing fill in the response

    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    strategy.get_display_state.return_value = {"session_pnl": 0.0}
    engine = _make_engine(broker, strategy)
    engine._grid_trade_map = {"4268.09_long": "1"}
    engine._log = MagicMock()

    engine._close_all_trades("session_profit_target")

    # Close-all still succeeded end-to-end; a closure event is still emitted.
    strategy.notify_close_all_complete.assert_called_once()
    events = _closed_events(engine._log)
    assert len(events) == 1
    assert events[0]["realized_pnl"] is None

    # A ledger row is still emitted (so the closure is visible on the card), but
    # with no P&L / unknown basis since the close response carried no fill.
    ledger = engine.get_realized_ledger()
    assert len(ledger) == 1
    assert ledger[0]["kind"] == "session_close"
    assert ledger[0]["realized_pnl"] is None
    assert ledger[0]["basis"] == "unknown"
