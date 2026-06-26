"""Regression guards (round 2) for the balance-delta P&L rewrite.

Covers fixes found by code review of the first cut:
- Fix 1: broker-side SL closures must feed _trade_pnls (risk-engine consecutive-
  loss gate + win-rate) with a per-trade P&L computed locally from entry + stop.
- Fix 2: margin-trim closes must bank P&L from the close response (close_trim).
- Fix 3: re-anchoring the session balance baseline on a new grid lifecycle.
- Fix 4: fail-closed on a sustained balance-read outage (halt after N).
- Per-trade entry capture works for market / limit / stop entry order types.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from aurex_trade.domain.models import ClosedTradeInfo
from aurex_trade.engine.trading_engine import TradeEntry, TradingEngine

_TEST_USER_ID = "test-user"


def _engine() -> tuple[TradingEngine, MagicMock]:
    broker = MagicMock()
    broker.equity = 100_000.0
    broker.get_account_summary.return_value = {
        "balance": 100_000.0, "unrealized_pnl": 0.0, "open_position_count": 0,
    }
    strategy = MagicMock()
    strategy.name = "ciby_sliding_grid"
    risk = MagicMock()
    risk._enabled = False
    engine = TradingEngine(
        strategy=strategy,
        risk_engine=risk,
        broker=broker,
        market_data=MagicMock(),
        repository=MagicMock(),
        symbol="XAU_USD",
        interval_seconds=0,
        bar_count=10,
        user_id=_TEST_USER_ID,
    )
    engine._log = MagicMock()
    return engine, broker


def _entry(side: str, entry_price: float, stop: float | None, qty: float = 2.0) -> TradeEntry:
    return TradeEntry(
        grid_key=f"{entry_price:.2f}_{'long' if side == 'buy' else 'short'}",
        side=side, quantity=qty, entry_price=entry_price, stop_loss=stop,
    )


class TestSLClosureFeedsLedger:
    def test_long_stop_loss_records_negative_pnl(self) -> None:
        engine, _ = _engine()
        engine._grid_trade_map = {"4000.00_long": "T1"}
        engine._trade_entry = {"T1": _entry("buy", 4000.0, 3990.0, qty=2.0)}
        engine._last_price = 3990.0
        # Trade T1 vanished from open trades -> stopped out.
        engine._check_closures([])
        # P&L = (stop 3990 - entry 4000) * 2 = -20.
        assert engine._trade_pnls == [-20.0]
        assert "T1" not in engine._trade_entry

    def test_short_stop_loss_records_pnl_with_correct_sign(self) -> None:
        engine, _ = _engine()
        engine._grid_trade_map = {"4000.00_short": "T2"}
        # Short stopped above entry -> loss. entry 4000, stop 4010, qty 2 -> -20.
        engine._trade_entry = {"T2": _entry("sell", 4000.0, 4010.0, qty=2.0)}
        engine._check_closures([])
        assert engine._trade_pnls == [-20.0]

    def test_consecutive_losses_reach_the_ledger(self) -> None:
        # The risk gate reads engine._trade_pnls -- prove a run of SL losses lands.
        engine, _ = _engine()
        for i in range(3):
            tid = f"T{i}"
            engine._grid_trade_map = {f"400{i}.00_long": tid}
            engine._trade_entry = {tid: _entry("buy", 4000.0 + i, 3990.0 + i, qty=1.0)}
            engine._check_closures([])
        assert len(engine._trade_pnls) == 3
        assert all(p < 0 for p in engine._trade_pnls)

    def test_no_entry_record_appends_nothing(self) -> None:
        # Never fabricate a 0.0 (would corrupt the consecutive-loss run).
        engine, _ = _engine()
        engine._grid_trade_map = {"4000.00_long": "T9"}
        engine._trade_entry = {}
        engine._last_price = 3990.0
        engine._check_closures([])
        assert engine._trade_pnls == []

    def test_falls_back_to_last_price_when_no_stop(self) -> None:
        engine, _ = _engine()
        engine._grid_trade_map = {"4000.00_long": "T8"}
        engine._trade_entry = {"T8": _entry("buy", 4000.0, None, qty=2.0)}
        engine._last_price = 3995.0
        engine._check_closures([])
        assert engine._trade_pnls == [-10.0]  # (3995 - 4000) * 2


class TestMarginTrimBanksPnl:
    def test_trim_uses_close_response_pnl_and_close_trim_reason(self) -> None:
        engine, broker = _engine()
        engine._strategy.get_levels_to_close.return_value = ["4000.00_long"]
        engine._grid_trade_map = {"4000.00_long": "TT"}
        engine._trade_entry = {"TT": _entry("buy", 4000.0, 3990.0)}
        broker.close_trade.return_value = ClosedTradeInfo(
            broker_trade_id="TT", close_price=4012.0, realized_pnl=24.0,
            close_reason="MARKET_ORDER",
        )

        engine._check_levels_to_close()

        # Exact P&L from the close response is banked.
        assert engine._trade_pnls == [24.0]
        # Reported to the strategy as a trim, not a stop-loss.
        engine._strategy.report_trade_closed.assert_called_once()
        args = engine._strategy.report_trade_closed.call_args.args
        assert args[0] == "4000.00_long"
        assert args[2] == "close_trim"
        # Maps cleaned so the next poll doesn't double-count it as an SL closure.
        assert "4000.00_long" not in engine._grid_trade_map
        assert "TT" not in engine._trade_entry

    def test_trimmed_trade_not_double_counted_by_check_closures(self) -> None:
        engine, broker = _engine()
        engine._strategy.get_levels_to_close.return_value = ["4000.00_long"]
        engine._grid_trade_map = {"4000.00_long": "TT"}
        engine._trade_entry = {"TT": _entry("buy", 4000.0, 3990.0)}
        broker.close_trade.return_value = ClosedTradeInfo(
            broker_trade_id="TT", close_price=4012.0, realized_pnl=24.0,
            close_reason="MARKET_ORDER",
        )
        engine._check_levels_to_close()
        # Now closure detection runs with the trade already gone from open trades.
        engine._check_closures([])
        # Still only the single trim P&L -- no phantom SL closure appended.
        assert engine._trade_pnls == [24.0]


class TestRealizedLedger:
    """The Realized P&L card's per-trade ledger (trims + stop-outs).

    Distinct from _trade_pnls (win-rate / risk gate): the ledger keeps every
    closure including those with unknown P&L, and records kind + basis for the UI.
    """

    def test_stop_out_appends_ledger_entry_with_basis(self) -> None:
        engine, _ = _engine()
        engine._grid_trade_map = {"4000.00_long": "T1"}
        engine._trade_entry = {"T1": _entry("buy", 4000.0, 3990.0, qty=2.0)}
        engine._check_closures([])
        ledger = engine.get_realized_ledger()
        assert len(ledger) == 1
        assert ledger[0]["kind"] == "stop_out"
        assert ledger[0]["realized_pnl"] == -20.0  # (3990 - 4000) * 2
        assert ledger[0]["basis"] == "stop_price"
        assert ledger[0]["broker_trade_id"] == "T1"
        assert ledger[0]["grid_level"] == "4000.00_long"

    def test_stop_out_with_no_entry_records_unknown_basis(self) -> None:
        # No entry record -> P&L unknown, but the closure is still ledgered.
        engine, _ = _engine()
        engine._grid_trade_map = {"4000.00_long": "T9"}
        engine._trade_entry = {}
        engine._last_price = None
        engine._check_closures([])
        ledger = engine.get_realized_ledger()
        assert len(ledger) == 1
        assert ledger[0]["kind"] == "stop_out"
        assert ledger[0]["realized_pnl"] is None
        assert ledger[0]["basis"] == "unknown"

    def test_trim_appends_exact_ledger_entry(self) -> None:
        engine, broker = _engine()
        engine._strategy.get_levels_to_close.return_value = ["4000.00_long"]
        engine._grid_trade_map = {"4000.00_long": "TT"}
        engine._trade_entry = {"TT": _entry("buy", 4000.0, 3990.0)}
        broker.close_trade.return_value = ClosedTradeInfo(
            broker_trade_id="TT", close_price=4012.0, realized_pnl=24.0,
            close_reason="MARKET_ORDER",
        )
        engine._check_levels_to_close()
        ledger = engine.get_realized_ledger()
        assert len(ledger) == 1
        assert ledger[0]["kind"] == "trim"
        assert ledger[0]["realized_pnl"] == 24.0
        assert ledger[0]["basis"] == "exact"
        assert ledger[0]["broker_trade_id"] == "TT"

    def test_trim_with_none_close_response_records_unknown_basis(self) -> None:
        # close_trade returns None (trade already gone / no per-trade fill) ->
        # P&L unknown, so basis must be "unknown", not "exact".
        engine, broker = _engine()
        engine._strategy.get_levels_to_close.return_value = ["4000.00_long"]
        engine._grid_trade_map = {"4000.00_long": "TT"}
        engine._trade_entry = {"TT": _entry("buy", 4000.0, 3990.0)}
        broker.close_trade.return_value = None
        engine._check_levels_to_close()
        ledger = engine.get_realized_ledger()
        assert len(ledger) == 1
        assert ledger[0]["kind"] == "trim"
        assert ledger[0]["realized_pnl"] is None
        assert ledger[0]["basis"] == "unknown"

    def test_session_end_does_not_append_ledger_entry(self) -> None:
        # close_all banks a session rollup; it must NOT add a per-trade ledger row
        # (the session delta already contains the trims -> avoid double-count).
        engine, broker = _engine()
        broker.get_open_trades.return_value = []
        engine._session_start_balance = 100_000.0
        engine._close_all_trades("session_profit_target")
        assert engine.get_realized_ledger() == []

    def test_get_realized_pnl_reads_cached_balance_delta(self) -> None:
        # The card header reads this cheap getter (no broker I/O), not get_metrics.
        engine, broker = _engine()
        engine._run_start_balance = 100_000.0
        engine._last_balance = 100_071.4
        assert engine.get_realized_pnl() == pytest.approx(71.4)
        # Falls back to 0.0 before anchors are seeded -- never touches the broker.
        engine._run_start_balance = None
        assert engine.get_realized_pnl() == 0.0
        broker.get_account_summary.assert_not_called()


class TestEntryCaptureAllOrderTypes:
    """_record_trade_entry is invoked at fill time for market / limit / stop."""

    def test_record_trade_entry_stores_record(self) -> None:
        engine, _ = _engine()
        engine._record_trade_entry("M1", "4000.00_long", "buy", 2.0, 4001.5, 3990.0)
        rec = engine._trade_entry["M1"]
        assert rec["side"] == "buy"
        assert rec["entry_price"] == 4001.5  # fill price, not trigger
        assert rec["stop_loss"] == 3990.0
        assert rec["quantity"] == 2.0

    def test_empty_broker_id_is_ignored(self) -> None:
        engine, _ = _engine()
        engine._record_trade_entry("", "4000.00_long", "buy", 2.0, 4000.0, 3990.0)
        assert engine._trade_entry == {}


class TestFailClosedBalanceReads:
    def _strategy_cycle_engine(self) -> tuple[TradingEngine, MagicMock]:
        engine, broker = _engine()
        # One bar so _run_strategy_cycle proceeds to the balance read.
        bar = MagicMock()
        bar.close = 4000.0
        bar.symbol = "XAU_USD"
        bar.timestamp = datetime(2025, 5, 1, 12, 0, tzinfo=UTC)
        broker.get_latest_bars.return_value = [bar]
        engine._market_data = broker
        engine._run_start_balance = 100_000.0
        engine._session_start_balance = 100_000.0
        engine._day_start_balance = 100_000.0
        engine._balance_day = datetime.now(UTC).strftime("%Y-%m-%d")
        engine._last_balance = 100_000.0
        engine._running = True
        return engine, broker

    def test_transient_failure_skips_cycle_does_not_halt(self) -> None:
        engine, broker = self._strategy_cycle_engine()
        broker.get_account_summary.side_effect = RuntimeError("503")
        engine._run_strategy_cycle()
        assert engine._balance_read_failures == 1
        assert engine._running is True  # one blip -> still running

    def test_sustained_failure_halts(self) -> None:
        engine, broker = self._strategy_cycle_engine()
        broker.get_account_summary.side_effect = RuntimeError("503")
        broker.cancel_all_orders.return_value = 0
        for _ in range(engine._MAX_BALANCE_READ_FAILURES):
            engine._run_strategy_cycle()
        assert engine._balance_read_failures >= engine._MAX_BALANCE_READ_FAILURES
        assert engine._running is False  # fail-closed halt

    def test_success_resets_failure_counter(self) -> None:
        engine, broker = self._strategy_cycle_engine()
        broker.get_account_summary.side_effect = [
            RuntimeError("503"),
            {"balance": 100_000.0, "unrealized_pnl": 0.0, "open_position_count": 0},
        ]
        engine._run_strategy_cycle()
        assert engine._balance_read_failures == 1
        # Strategy.generate must return something benign on the successful cycle.
        engine._strategy.generate.return_value = None
        engine._run_strategy_cycle()
        assert engine._balance_read_failures == 0
        assert engine._running is True
