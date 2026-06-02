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


def _drain_all(strategy: CibyHedgedDoublingGridStrategy, bars: list[BarData]) -> list[Signal]:
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
    """Simulate both sides of a hedged pair filling at a level."""
    level_str = f"{level:.2f}"
    strategy.report_fill(f"{level_str}_buy", level)
    strategy.report_fill(f"{level_str}_sell", level)


class TestInitialization:
    """Test strategy initialization and hedged pair placement."""

    def test_first_call_sets_anchor_and_queues_8_signals(self) -> None:
        """4 levels x 2 signals (buy + sell) = 8 signals."""
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        assert strategy._anchor_price == 23.0
        # 4 levels: 13, 3, 33, 43 → 8 limit orders (buy + sell each)
        assert len(signals) == 8

    def test_all_signals_are_limit_orders_with_no_stop_loss(self) -> None:
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        for sig in signals:
            assert sig.metadata["order_type"] == "LIMIT"
            assert sig.stop_loss is None
            assert sig.take_profit is None

    def test_grid_levels_are_correct(self) -> None:
        """spacing=10, anchor=23 → levels at 33, 43 (above) and 13, 3 (below)."""
        strategy = _make_strategy(spacing=10.0)
        _drain_all(strategy, _bars(23.0))

        assert sorted(strategy._levels_above) == [33.0, 43.0]
        assert sorted(strategy._levels_below, reverse=True) == [13.0, 3.0]

    def test_both_buy_and_sell_at_each_level(self) -> None:
        strategy = _make_strategy(spacing=10.0)
        signals = _drain_all(strategy, _bars(23.0))

        # Group by level price
        levels_seen: dict[str, set[str]] = {}
        for sig in signals:
            price = sig.metadata["limit_price"]
            if price not in levels_seen:
                levels_seen[price] = set()
            if sig.signal_type == SignalType.LONG:
                levels_seen[price].add("buy")
            else:
                levels_seen[price].add("sell")

        for price, sides in levels_seen.items():
            assert sides == {"buy", "sell"}, f"Level {price} missing side: {sides}"


class TestScenario1BreakoutDownThenRally:
    """Price starts at 23, drops to outer below (3), then rallies to take profit.

    spacing=10, units=2, anchor=23
    Levels: above=[33, 43], below=[13, 3]
    - Price drops to 13 → hedged pair fills (net $0)
    - Price drops to 3 → hedged pair fills + doubled BUY 2@3 (trailing stop)
    - Price rallies to 23 (3 + 2*spacing=23) → take profit, close all
    Expected P&L on doubled buy: 2 * (23-3) = +$40
    """

    def test_breakout_down_then_rally(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        # Initialize
        _drain_all(strategy, _bars(23.0))

        # Price drops to inner level 13 — fill both sides
        _fill_level(strategy, 13.0)

        # Drain any signals from fill (should be none for inner level)
        inner_signals = _drain_all(strategy, _bars(13.0))
        doubled_signals = [
            s for s in inner_signals
            if "doubled" in s.metadata.get("grid_level", "")
        ]
        assert len(doubled_signals) == 0  # No doubling at inner level

        # Price drops to outer level 3 — fill both sides
        _fill_level(strategy, 3.0)

        # Now drain — should get the doubled BUY signal
        outer_signals = _drain_all(strategy, _bars(3.0))
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

        # Verify take profit triggers at 3 + 2*10 = 23
        assert strategy._doubled_active is True
        assert strategy._doubled_level == 3.0
        assert strategy._check_take_profit(22.9) is False
        assert strategy._check_take_profit(23.0) is True


class TestScenario2BreakoutUpThenDrop:
    """Mirror of scenario 1 — price rises to outer above then drops.

    spacing=10, anchor=23
    Levels: above=[33, 43], below=[13, 3]
    - Price rises to 33 → hedged pair fills
    - Price rises to 43 → hedged pair fills + doubled SELL 2@43
    - Price drops to 23 (43 - 2*spacing=23) → take profit
    """

    def test_breakout_up_then_drop(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        # Fill inner above (33)
        _fill_level(strategy, 33.0)
        _drain_all(strategy, _bars(33.0))

        # Fill outer above (43)
        _fill_level(strategy, 43.0)
        signals = _drain_all(strategy, _bars(43.0))

        doubled_signals = [s for s in signals if "doubled" in s.metadata.get("grid_level", "")]
        assert len(doubled_signals) == 1
        doubled = doubled_signals[0]
        assert doubled.signal_type == SignalType.SHORT  # Sell (betting on reversal)
        assert doubled.metadata["order_type"] == "MARKET"
        assert doubled.metadata["trailing_stop_distance"] == "10.00000"

        # Take profit at 43 - 2*10 = 23
        assert strategy._check_take_profit(23.1) is False
        assert strategy._check_take_profit(23.0) is True


class TestScenario3FlatAfterDoubling:
    """Price reaches outer level, doubled buy placed, price stays flat.

    Expected: $0 P&L. No bleeding, strategy waits.
    """

    def test_flat_after_doubling(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0)
        _drain_all(strategy, _bars(23.0))

        _fill_level(strategy, 13.0)
        _drain_all(strategy, _bars(13.0))

        _fill_level(strategy, 3.0)
        _drain_all(strategy, _bars(3.0))

        # Price stays at 3 — no take profit, no session loss
        assert strategy._check_take_profit(3.0) is False
        signals = _drain_all(strategy, _bars(3.0))
        # No FLAT signals — just waiting
        flat_signals = [s for s in signals if s.signal_type == SignalType.FLAT]
        assert len(flat_signals) == 0


class TestScenario4AdverseContinuation:
    """Doubled buy at 3, price continues dropping. Session loss limit triggers.

    session_loss_limit=100, units=2
    Doubled buy at 3, price drops. Engine reports unrealized loss.
    When total P&L <= -100, close all.
    """

    def test_adverse_continuation_triggers_session_loss(self) -> None:
        strategy = _make_strategy(
            spacing=10.0, units=2.0, session_loss_limit=100.0
        )
        _drain_all(strategy, _bars(23.0))

        _fill_level(strategy, 13.0)
        _drain_all(strategy, _bars(13.0))
        _fill_level(strategy, 3.0)
        _drain_all(strategy, _bars(3.0))

        # Simulate adverse move — engine reports unrealized loss
        strategy.update_unrealized_pnl(-101.0)

        # Next generate should trigger close-all (single call, not drain)
        sig = strategy.generate(_bars(-50.0))
        assert sig is not None
        assert sig.signal_type == SignalType.FLAT
        assert sig.metadata["reason"] == "session_loss_limit"


class TestScenario5TrailingStopCapture:
    """Doubled buy at 3, price rallies to 13 (+$10 profit), then reverses.

    Trailing stop activates (managed by broker). When broker closes the trade,
    strategy marks doubled as inactive. P&L captured.
    """

    def test_trailing_stop_closure_marks_doubled_inactive(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        _fill_level(strategy, 13.0)
        _drain_all(strategy, _bars(13.0))
        _fill_level(strategy, 3.0)
        _drain_all(strategy, _bars(3.0))

        assert strategy._doubled_active is True
        assert strategy._doubled_grid_key == "3.00_doubled"

        # Broker closes the doubled position (trailing stop hit)
        strategy.report_trade_closed("3.00_doubled", 20.0)  # 2 units * $10 profit

        assert strategy._doubled_active is False
        assert strategy._session_realized_pnl == 20.0

        # Take profit no longer triggers (doubled inactive)
        assert strategy._check_take_profit(23.0) is False


class TestScenario6SlowRangeNoLevelsHit:
    """Price oscillates within inner levels, never reaches outer.

    spacing=20, anchor=23 → levels at 43, 63 (above), 3, -17 (below)
    Price stays between 5-41 → inner levels (43, 3) may fill but outer never hit.
    No doubling, near-zero P&L.
    """

    def test_slow_range_no_doubling(self) -> None:
        strategy = _make_strategy(spacing=20.0, units=2.0)
        _drain_all(strategy, _bars(23.0))

        # Only inner levels fill (3 below, 43 above)
        # Fill inner below (3) — this is actually the INNER level (anchor-spacing=3)
        _fill_level(strategy, 3.0)
        signals = _drain_all(strategy, _bars(3.0))

        # 3.0 is inner below (levels_below = [3.0, -17.0])
        # No doubling should trigger at inner level
        doubled_signals = [s for s in signals if "doubled" in s.metadata.get("grid_level", "")]
        assert len(doubled_signals) == 0
        assert strategy._doubled_level is None


class TestScenario7WhipsawDetectionAndPause:
    """Same level re-triggers 3 times → session pauses.

    whipsaw_limit=3
    """

    def test_whipsaw_pauses_session(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, whipsaw_limit=3)
        _drain_all(strategy, _bars(23.0))

        # First trigger at level 13
        _fill_level(strategy, 13.0)
        _drain_all(strategy, _bars(13.0))
        assert strategy._session_paused is False

        # Simulate level being released and re-filled (whipsaw)
        # Reset the level state to simulate re-trigger
        strategy._level_pair_complete.pop(13.0, None)
        strategy._placed_levels.discard(13.0)

        # Second trigger
        strategy.report_fill("13.00_buy", 13.0)
        assert strategy._session_paused is False
        strategy.report_fill("13.00_sell", 13.0)
        _drain_all(strategy, _bars(13.0))

        # Reset again
        strategy._level_pair_complete.pop(13.0, None)
        strategy._placed_levels.discard(13.0)

        # Third trigger — should pause
        strategy.report_fill("13.00_buy", 13.0)
        assert strategy._session_paused is True
        assert strategy._close_all_pending is True

        # Next generate should emit FLAT close-all (single call, not drain)
        sig = strategy.generate(_bars(13.0))
        assert sig is not None
        assert sig.signal_type == SignalType.FLAT
        assert sig.metadata["reason"] == "whipsaw_pause"


class TestScenario8MultipleLevelsFillInSequence:
    """Price drops through inner (13) then outer (3). Only outer triggers doubling.

    Verify: correct number of hedged pairs + exactly one doubled position.
    """

    def test_sequential_fills_only_outer_doubles(self) -> None:
        strategy = _make_strategy(spacing=10.0, units=2.0, trailing_stop_distance=10.0)
        _drain_all(strategy, _bars(23.0))

        # Fill inner level first
        _fill_level(strategy, 13.0)
        inner_signals = _drain_all(strategy, _bars(13.0))
        inner_doubled = [s for s in inner_signals if "doubled" in s.metadata.get("grid_level", "")]
        assert len(inner_doubled) == 0

        # Fill outer level
        _fill_level(strategy, 3.0)
        outer_signals = _drain_all(strategy, _bars(3.0))
        outer_doubled = [s for s in outer_signals if "doubled" in s.metadata.get("grid_level", "")]
        assert len(outer_doubled) == 1

        # Verify strategy state
        assert strategy._doubled_level == 3.0
        assert strategy._doubled_side == "long"
        assert strategy._doubled_active is True

        # Verify only one doubled position was triggered
        assert strategy._doubled_grid_key == "3.00_doubled"


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
        _fill_level(strategy, 13.0)
        _drain_all(strategy, _bars(13.0))
        _fill_level(strategy, 3.0)
        _drain_all(strategy, _bars(3.0))

        state = strategy.get_display_state()
        assert state is not None
        assert state["doubled_level"] == 3.0
        assert state["doubled_side"] == "long"
        assert state["doubled_active"] is True


class TestNotifyCloseAllComplete:
    """Test session restart and whipsaw-stop behavior."""

    def test_restart_resets_state(self) -> None:
        strategy = _make_strategy(spacing=10.0)
        _drain_all(strategy, _bars(23.0))
        _fill_level(strategy, 13.0)
        _drain_all(strategy, _bars(13.0))

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
        strategy.report_fill("13.00_buy", 13.0)
        assert strategy._session_paused is True

        # After close-all completes, session should stay inactive
        strategy._close_all_in_progress = True
        strategy.notify_close_all_complete()
        assert strategy._session_active is False
