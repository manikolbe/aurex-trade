"""Unit tests for CibyHedgedDoublingGridStrategy — breakout capture."""

from datetime import datetime

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData, Signal
from aurex_trade.domain.strategy.ciby_hedged_doubling_grid import (
    CibyHedgedDoublingGridStrategy,
)


def _bar(price: float, symbol: str = "XAU_USD", day: str = "2025-05-01") -> BarData:
    """Create a BarData with the given close price."""
    return BarData(
        timestamp=datetime.fromisoformat(f"{day}T12:00:00+00:00"),
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100.0,
        symbol=symbol,
    )


def _bars(price: float) -> list[BarData]:
    """Create a single-element bar list at given price."""
    return [_bar(price)]


def _drain_all(
    strategy: CibyHedgedDoublingGridStrategy, bars: list[BarData]
) -> list[Signal]:
    """Drain all signals from the strategy's queue."""
    signals: list[Signal] = []
    while True:
        sig = strategy.generate(bars)
        if sig is None:
            break
        signals.append(sig)
    return signals


def _make_strategy(
    spacing: float = 10.0,
    units: float = 2.0,
    trailing_stop_distance: float = 10.0,
    session_loss_limit: float = 100.0,
    whipsaw_limit: int = 3,
) -> CibyHedgedDoublingGridStrategy:
    """Create strategy with test-friendly defaults."""
    return CibyHedgedDoublingGridStrategy(
        spacing=spacing,
        units=units,
        trailing_stop_distance=trailing_stop_distance,
        session_loss_limit=session_loss_limit,
        whipsaw_limit=whipsaw_limit,
    )


def _fill_level(strategy: CibyHedgedDoublingGridStrategy, level: float) -> None:
    """Simulate limit fill + opposite market fill at a level (full hedged pair)."""
    level_str = f"{level:.2f}"
    # First fill is the limit side (triggers placed → active transition)
    # Second fill is the opposite market side placed by engine
    strategy.report_fill(f"{level_str}_long", level)
    strategy.report_fill(f"{level_str}_short", level)


class TestInitialization:
    """Test strategy initialization and limit order placement."""

    def test_first_call_sets_anchor_and_queues_4_signals(self) -> None:
        """4 levels x 1 limit each = 4 signals (not 8)."""
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        assert strategy._anchor_price == 23.0
        # 4 levels, one limit per level
        assert len(signals) == 4

    def test_all_signals_are_limit_orders_with_no_stop_loss(self) -> None:
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        for sig in signals:
            assert sig.metadata["order_type"] == "LIMIT"
            assert sig.stop_loss is None
            assert sig.take_profit is None

    def test_grid_levels_are_correct(self) -> None:
        """spacing=10, anchor=23 -> levels at 30, 40 (above) and 20, 10 (below)."""
        strategy = _make_strategy(spacing=10.0)
        _drain_all(strategy, _bars(23.0))

        assert sorted(strategy._levels_above) == [30.0, 40.0]
        assert sorted(strategy._levels_below, reverse=True) == [20.0, 10.0]

    def test_correct_limit_side_per_level(self) -> None:
        """Above price -> sell limit (waits for rise). Below -> buy limit (waits for drop)."""
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        for sig in signals:
            limit_price = float(sig.metadata["limit_price"])
            if limit_price > 23.0:
                # Above price: sell limit
                assert sig.signal_type == SignalType.SHORT
                assert sig.metadata["opposite_side"] == "BUY"
            else:
                # Below price: buy limit
                assert sig.signal_type == SignalType.LONG
                assert sig.metadata["opposite_side"] == "SELL"

    def test_opposite_side_metadata_present(self) -> None:
        """Each signal has opposite_side and opposite_grid_level for engine."""
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        for sig in signals:
            assert "opposite_side" in sig.metadata
            assert "opposite_grid_level" in sig.metadata
            # No stop loss on opposite side either
            assert sig.metadata["opposite_stop_loss"] == ""


class TestScenario1BreakoutDownThenRally:
    """Price starts at 23, drops to outer below (3), then rallies to take profit.

    spacing=10, units=2, anchor=23
    Levels: above=[33, 43], below=[13, 3]
    - Price drops to 13 -> limit fills, engine places opposite market
    - Price drops to 3 -> same, then doubled BUY triggered
    - Price rallies to 23 (3 + 2*spacing) -> take profit
    """

    def test_breakout_down_then_rally(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        # Price drops to inner level 13 — both sides fill
        _fill_level(strategy, 20.0)
        inner_signals = _drain_all(strategy, _bars(20.0))
        doubled_signals = [
            s for s in inner_signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(doubled_signals) == 0  # No doubling at inner level

        # Price drops to outer level 3 — both sides fill
        _fill_level(strategy, 10.0)
        outer_signals = _drain_all(strategy, _bars(10.0))
        doubled_signals = [
            s for s in outer_signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(doubled_signals) == 1
        doubled = doubled_signals[0]
        assert doubled.signal_type == SignalType.LONG  # Buy (betting on bounce)
        assert doubled.metadata["order_type"] == "MARKET"
        assert doubled.metadata["trailing_stop_distance"] == "10.00000"
        assert doubled.metadata["fixed_units"] == "2.0"

        # Take profit at 3 + 2*10 = 23
        assert strategy._doubled_active is True
        assert strategy._doubled_level == 10.0
        assert strategy._check_take_profit(29.9) is False
        assert strategy._check_take_profit(30.0) is True


class TestScenario2BreakoutUpThenDrop:
    """Mirror of scenario 1 — price rises to outer above then drops.

    spacing=10, anchor=23
    Levels: above=[33, 43], below=[13, 3]
    - Price rises to 33 -> hedged pair fills
    - Price rises to 43 -> hedged pair fills + doubled SELL
    - Price drops to 23 (43 - 2*spacing) -> take profit
    """

    def test_breakout_up_then_drop(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        # Fill inner above (33)
        _fill_level(strategy, 30.0)
        _drain_all(strategy, _bars(30.0))

        # Fill outer above (43)
        _fill_level(strategy, 40.0)
        signals = _drain_all(strategy, _bars(40.0))

        doubled_signals = [
            s for s in signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(doubled_signals) == 1
        doubled = doubled_signals[0]
        assert doubled.signal_type == SignalType.SHORT  # Sell (betting on reversal)
        assert doubled.metadata["order_type"] == "MARKET"
        assert doubled.metadata["trailing_stop_distance"] == "10.00000"

        # Take profit at 43 - 2*10 = 23
        assert strategy._check_take_profit(20.1) is False
        assert strategy._check_take_profit(20.0) is True


class TestScenario3FlatAfterDoubling:
    """Price reaches outer level, doubled buy placed, price stays flat.

    Expected: $0 P&L. No bleeding, strategy waits.
    """

    def test_flat_after_doubling(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0)
        _drain_all(strategy, _bars(23.0))

        _fill_level(strategy, 20.0)
        _drain_all(strategy, _bars(20.0))

        _fill_level(strategy, 10.0)
        _drain_all(strategy, _bars(10.0))

        # Price stays at 3 — no take profit, no session loss
        assert strategy._check_take_profit(10.0) is False
        signals = _drain_all(strategy, _bars(10.0))
        flat_signals = [s for s in signals if s.signal_type == SignalType.FLAT]
        assert len(flat_signals) == 0


class TestScenario4AdverseContinuation:
    """Doubled buy at 3, price continues dropping. Session loss limit triggers."""

    def test_adverse_continuation_triggers_session_loss(self) -> None:
        strategy = _make_strategy(
            spacing=10.0, units=2.0, session_loss_limit=100.0
        )
        _drain_all(strategy, _bars(23.0))

        _fill_level(strategy, 20.0)
        _drain_all(strategy, _bars(20.0))
        _fill_level(strategy, 10.0)
        _drain_all(strategy, _bars(10.0))

        # Simulate adverse move — engine reports unrealized loss
        strategy.update_unrealized_pnl(-101.0)

        # Next generate should trigger close-all (single call, not drain)
        sig = strategy.generate(_bars(-50.0))
        assert sig is not None
        assert sig.signal_type == SignalType.FLAT
        assert sig.metadata["reason"] == "session_loss_limit"


class TestScenario5TrailingStopCapture:
    """Doubled buy at 3, trailing stop managed by broker. When closed, mark inactive."""

    def test_trailing_stop_closure_marks_doubled_inactive(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        _fill_level(strategy, 20.0)
        _drain_all(strategy, _bars(20.0))
        _fill_level(strategy, 10.0)
        _drain_all(strategy, _bars(10.0))

        assert strategy._doubled_active is True
        assert strategy._doubled_grid_key == "10.00_doubled"

        # Broker closes the doubled position (trailing stop hit)
        strategy.report_trade_closed("10.00_doubled", 20.0)

        assert strategy._doubled_active is False
        assert strategy._session_realized_pnl == 20.0

        # Take profit no longer triggers (doubled inactive)
        assert strategy._check_take_profit(30.0) is False


class TestScenario6SlowRangeNoLevelsHit:
    """Price oscillates within inner levels, never reaches outer.

    spacing=20, anchor=23 -> levels at 40, 60 (above), 20, 0 (below)
    Only inner level (20) fills — no doubling.
    """

    def test_slow_range_no_doubling(self) -> None:
        strategy = _make_strategy(spacing=20.0, units=2.0)
        _drain_all(strategy, _bars(23.0))

        # Fill inner below (20) — this is the inner level
        _fill_level(strategy, 20.0)
        signals = _drain_all(strategy, _bars(20.0))

        # No doubling at inner level
        doubled_signals = [
            s for s in signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(doubled_signals) == 0
        assert strategy._doubled_level is None


class TestScenario7WhipsawDetectionAndPause:
    """Same level re-triggers 3 times -> session pauses."""

    def test_whipsaw_pauses_session(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, whipsaw_limit=3)
        _drain_all(strategy, _bars(23.0))

        # First trigger at level 13 (limit fills)
        strategy.report_fill("20.00_long", 20.0)
        assert strategy._session_paused is False

        # Simulate level release (both sides closed) and re-placement
        strategy._filled_levels.pop(20.0, None)
        strategy._filled_entry_prices.pop(20.0, None)
        strategy._placed_levels.add(20.0)  # maintenance re-places it

        # Second trigger
        strategy.report_fill("20.00_long", 20.0)
        assert strategy._session_paused is False

        # Reset again
        strategy._filled_levels.pop(20.0, None)
        strategy._filled_entry_prices.pop(20.0, None)
        strategy._placed_levels.add(20.0)

        # Third trigger — should pause
        strategy.report_fill("20.00_long", 20.0)
        assert strategy._session_paused is True
        assert strategy._close_all_pending is True

        # Next generate should emit FLAT close-all
        sig = strategy.generate(_bars(13.0))
        assert sig is not None
        assert sig.signal_type == SignalType.FLAT
        assert sig.metadata["reason"] == "whipsaw_pause"


class TestScenario8MultipleLevelsFillInSequence:
    """Price drops through inner (13) then outer (3). Only outer triggers doubling."""

    def test_sequential_fills_only_outer_doubles(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        # Fill inner level first
        _fill_level(strategy, 20.0)
        inner_signals = _drain_all(strategy, _bars(20.0))
        inner_doubled = [
            s for s in inner_signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(inner_doubled) == 0

        # Fill outer level
        _fill_level(strategy, 10.0)
        outer_signals = _drain_all(strategy, _bars(10.0))
        outer_doubled = [
            s for s in outer_signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(outer_doubled) == 1

        # Verify strategy state
        assert strategy._doubled_level == 10.0
        assert strategy._doubled_side == "long"
        assert strategy._doubled_active is True
        assert strategy._doubled_grid_key == "10.00_doubled"


class TestDisplayState:
    """Test get_display_state returns correct structure."""

    def test_display_state_before_init_is_none(self) -> None:
        strategy = _make_strategy()
        assert strategy.get_display_state() is None

    def test_display_state_after_init(self) -> None:
        strategy = _make_strategy(spacing=10.0)
        _drain_all(strategy, _bars(23.0))

        state = strategy.get_display_state()
        assert state is not None
        assert state["type"] == "doubled_grid"
        assert state["anchor_price"] == 23.0
        assert state["session_paused"] is False
        assert state["doubled_level"] is None
        assert len(state["grid_levels"]) == 4  # type: ignore[arg-type]

    def test_display_state_with_doubled(self) -> None:
        strategy = _make_strategy(spacing=10.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))
        _fill_level(strategy, 20.0)
        _drain_all(strategy, _bars(20.0))
        _fill_level(strategy, 10.0)
        _drain_all(strategy, _bars(10.0))

        state = strategy.get_display_state()
        assert state is not None
        assert state["doubled_level"] == 10.0
        assert state["doubled_side"] == "long"
        assert state["doubled_active"] is True


class TestNotifyCloseAllComplete:
    """Test session restart and whipsaw-stop behavior."""

    def test_restart_resets_state(self) -> None:
        strategy = _make_strategy(spacing=10.0)
        _drain_all(strategy, _bars(23.0))
        _fill_level(strategy, 20.0)
        _drain_all(strategy, _bars(20.0))

        strategy._trigger_close_all("session_loss_limit")
        strategy.notify_close_all_complete()

        # Should have reset
        assert strategy._anchor_price is None
        assert strategy._doubled_level is None
        assert strategy._session_realized_pnl == 0.0

    def test_whipsaw_pause_stays_inactive(self) -> None:
        strategy = _make_strategy(spacing=10.0, whipsaw_limit=1)
        _drain_all(strategy, _bars(23.0))

        # One fill triggers whipsaw (limit=1)
        strategy.report_fill("20.00_long", 20.0)
        assert strategy._session_paused is True

        # After close-all completes, session should stay inactive
        strategy._close_all_in_progress = True
        strategy.notify_close_all_complete()
        assert strategy._session_active is False
