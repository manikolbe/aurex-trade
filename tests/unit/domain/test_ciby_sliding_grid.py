"""Unit tests for CibySlidingGridStrategy."""

from datetime import datetime

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData
from aurex_trade.domain.strategy.ciby_sliding_grid import CibySlidingGridStrategy


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


def _drain_all(strategy: CibySlidingGridStrategy, bars: list[BarData]) -> list[object]:
    """Drain all queued signals from the strategy."""
    signals: list[object] = []
    while True:
        sig = strategy.generate(bars)
        if sig is None:
            break
        signals.append(sig)
    return signals


def _new() -> CibySlidingGridStrategy:
    """Strategy with the sample's params: spacing 10, anchor gap 15, offset 0.90."""
    return CibySlidingGridStrategy(
        grid_spacing=10.0,
        anchor_gap=15.0,
        buy_sell_offset=0.90,
        anchor_units=10.0,
        grid_units=20.0,
        stop_buffer=1.0,
    )


def _by_key(signals: list[object]) -> dict[str, object]:
    """Index signals by their grid_level metadata key."""
    return {s.metadata["grid_level"]: s for s in signals}  # type: ignore[attr-defined]


class TestInitialization:
    def test_first_call_sets_anchor(self) -> None:
        strategy = _new()
        strategy.generate([_bar(4100.0)])
        assert strategy._anchor_price == 4100.0

    def test_anchor_pair_is_market(self) -> None:
        """The anchor sits at price, so its pair is placed at market."""
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)

        anchor_buy = by_key["4100.00_long"]
        anchor_sell = by_key["4100.00_short"]
        assert anchor_buy.metadata["order_type"] == "MARKET"  # type: ignore[attr-defined]
        assert anchor_sell.metadata["order_type"] == "MARKET"  # type: ignore[attr-defined]
        assert "limit_price" not in anchor_buy.metadata  # type: ignore[attr-defined]

    def test_ladder_geometry(self) -> None:
        """First level +/-15 from anchor, every level beyond +/-10."""
        strategy = _new()
        strategy.generate([_bar(4100.0)])
        # The ladder includes the anchor and levels at the right spacing.
        levels = strategy._levels
        assert 4100.0 in levels
        assert 4115.0 in levels  # anchor + 15
        assert 4125.0 in levels  # + 10
        assert 4085.0 in levels  # anchor - 15
        assert 4075.0 in levels  # - 10
        # No level between anchor and the first gap.
        assert 4110.0 not in levels
        assert 4090.0 not in levels

    def test_opening_shape_two_above_one_below(self) -> None:
        """Opening stance: anchor + 2 levels above + 1 below, both sides each."""
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        keys = {s.metadata["grid_level"] for s in signals}  # type: ignore[attr-defined]
        # At the anchor, direction defaults to up: 2 ahead (above) + 1 behind (below).
        assert keys == {
            "4100.00_long", "4100.00_short",
            "4115.00_long", "4115.00_short",
            "4125.00_long", "4125.00_short",
            "4085.00_long", "4085.00_short",
        }
        # The 2nd level below (4075) is NOT placed at the opening.
        assert "4075.00_long" not in keys
        assert "4075.00_short" not in keys


class TestPlacementGeometry:
    def test_buy_rests_offset_above_sell(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        buy = by_key["4115.00_long"]
        sell = by_key["4115.00_short"]
        assert float(buy.metadata["limit_price"]) == 4115.90  # type: ignore[attr-defined]
        assert float(sell.metadata["limit_price"]) == 4115.00  # type: ignore[attr-defined]

    def test_order_type_above_price(self) -> None:
        """Above price: buy is a STOP (breakout side), sell is a LIMIT."""
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        assert by_key["4115.00_long"].metadata["order_type"] == "STOP"  # type: ignore[attr-defined]
        assert by_key["4115.00_short"].metadata["order_type"] == "LIMIT"  # type: ignore[attr-defined]

    def test_order_type_below_price(self) -> None:
        """Below price: buy is a LIMIT, sell is a STOP (breakout side)."""
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        assert by_key["4085.00_long"].metadata["order_type"] == "LIMIT"  # type: ignore[attr-defined]
        assert by_key["4085.00_short"].metadata["order_type"] == "STOP"  # type: ignore[attr-defined]

    def test_signal_types(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        assert by_key["4115.00_long"].signal_type == SignalType.LONG  # type: ignore[attr-defined]
        assert by_key["4115.00_short"].signal_type == SignalType.SHORT  # type: ignore[attr-defined]


class TestUnits:
    def test_anchor_uses_anchor_units(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        assert float(by_key["4100.00_long"].metadata["fixed_units"]) == 10.0  # type: ignore[attr-defined]

    def test_other_levels_use_grid_units(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        assert float(by_key["4115.00_long"].metadata["fixed_units"]) == 20.0  # type: ignore[attr-defined]
        assert float(by_key["4085.00_short"].metadata["fixed_units"]) == 20.0  # type: ignore[attr-defined]


class TestStopLossRule:
    """SL distance from entry = (gap to next level in losing direction) + buffer."""

    def test_anchor_stops(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        # Buy 4100.90, next level below is 4085 (gap 15) → SL 4100.90 - 16 = 4084.90
        assert by_key["4100.00_long"].stop_loss == 4084.90  # type: ignore[attr-defined]
        # Sell 4100.00, next level above is 4115 (gap 15) → SL 4100 + 16 = 4116.00
        assert by_key["4100.00_short"].stop_loss == 4116.00  # type: ignore[attr-defined]

    def test_first_level_above_stops(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        # Buy 4115.90, next below is 4100 (gap 15) → SL 4099.90
        assert by_key["4115.00_long"].stop_loss == 4099.90  # type: ignore[attr-defined]
        # Sell 4115.00, next above is 4125 (gap 10) → SL 4126.00
        assert by_key["4115.00_short"].stop_loss == 4126.00  # type: ignore[attr-defined]

    def test_first_level_below_stops(self) -> None:
        strategy = _new()
        signals = _drain_all(strategy, [_bar(4100.0)])
        by_key = _by_key(signals)
        # Buy 4085.90, next below is 4075 (gap 10) → SL 4074.90
        assert by_key["4085.00_long"].stop_loss == 4074.90  # type: ignore[attr-defined]
        # Sell 4085.00, next above is 4100 (gap 15) → SL 4101.00
        assert by_key["4085.00_short"].stop_loss == 4101.00  # type: ignore[attr-defined]


class TestWindowSlides:
    def test_next_level_rests_as_price_advances(self) -> None:
        """A level further out is placed once price advances toward the edge."""
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        # Initially 4135 is not yet placed (only 4115, 4125 above).
        assert 4135.0 not in strategy._placed
        # Price rises near 4125 — the next level out (4135) should now rest.
        new_signals = _drain_all(strategy, [_bar(4126.0)])
        new_keys = {s.metadata["grid_level"] for s in new_signals}  # type: ignore[attr-defined]
        assert "4135.00_long" in new_keys
        assert "4135.00_short" in new_keys


class TestRefillMissingSide:
    def test_only_missing_side_replaced_on_revisit(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])

        # Both sides of 4115 fill, then the long side is stopped out.
        strategy.report_fill("4115.00_long", 4115.90)
        strategy.report_fill("4115.00_short", 4115.00)
        strategy.report_trade_closed("4115.00_long", -16.0, "close_sl")

        assert "long" in strategy._stopped.get(4115.0, set())
        assert "short" in strategy._filled.get(4115.0, set())

        # Price returns to 4115 — only the missing long side is re-queued.
        new_signals = _drain_all(strategy, [_bar(4118.0)])
        refills = [
            s for s in new_signals
            if s.metadata["grid_level"] == "4115.00_long"  # type: ignore[attr-defined]
        ]
        shorts = [
            s for s in new_signals
            if s.metadata["grid_level"] == "4115.00_short"  # type: ignore[attr-defined]
        ]
        assert len(refills) == 1
        assert len(shorts) == 0


class TestTrimming:
    """Active levels beyond the caps are retired (closed) to free margin."""

    def _fill(self, strategy: CibySlidingGridStrategy, level: float) -> None:
        """Mark both sides of a level as filled."""
        strategy.report_fill(f"{level:.2f}_long", level + 0.90)
        strategy.report_fill(f"{level:.2f}_short", level)

    def test_third_above_level_trims_trailing(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        # Price climbs; levels 4115, 4125 fill.
        self._fill(strategy, 4115.0)
        self._fill(strategy, 4125.0)
        # Price reaches 4135 and it fills — now 3 active above anchor (cap is 2).
        _drain_all(strategy, [_bar(4135.0)])
        self._fill(strategy, 4135.0)
        # Next cycle trims the trailing (nearest-anchor) above level: 4115.
        _drain_all(strategy, [_bar(4136.0)])
        to_close = strategy.get_levels_to_close()
        assert "4115.00_long" in to_close
        assert "4115.00_short" in to_close
        assert 4115.0 in strategy._retired

    def test_anchor_never_trimmed(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        self._fill(strategy, 4100.0)  # anchor fills
        self._fill(strategy, 4115.0)
        self._fill(strategy, 4125.0)
        _drain_all(strategy, [_bar(4135.0)])
        self._fill(strategy, 4135.0)
        _drain_all(strategy, [_bar(4136.0)])
        strategy.get_levels_to_close()
        # Anchor stays active and is never retired.
        assert 4100.0 not in strategy._retired
        assert "long" in strategy._filled.get(4100.0, set())

    def test_retired_level_not_reopened_on_close(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        self._fill(strategy, 4115.0)
        self._fill(strategy, 4125.0)
        _drain_all(strategy, [_bar(4135.0)])
        self._fill(strategy, 4135.0)
        _drain_all(strategy, [_bar(4136.0)])
        strategy.get_levels_to_close()
        # The engine closes the trimmed trades and reports them back.
        strategy.report_trade_closed("4115.00_long", 5.0, "trim")
        strategy.report_trade_closed("4115.00_short", -5.0, "trim")
        # A retired level is NOT marked stopped, so it won't be re-placed.
        assert "long" not in strategy._stopped.get(4115.0, set())
        assert "short" not in strategy._stopped.get(4115.0, set())
        # Even if price revisits 4115, no new orders are queued there.
        new_signals = _drain_all(strategy, [_bar(4116.0)])
        keys = {s.metadata["grid_level"] for s in new_signals}  # type: ignore[attr-defined]
        assert "4115.00_long" not in keys
        assert "4115.00_short" not in keys

    def test_realized_pnl_accrues_from_trim(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        before = strategy._session_realized_pnl
        strategy.report_trade_closed("4115.00_long", 12.0, "trim")
        assert strategy._session_realized_pnl == before + 12.0


class TestSessionExits:
    def test_profit_target_emits_close_all(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        strategy.update_unrealized_pnl(150.0)  # exceeds default 100 target
        sig = strategy.generate([_bar(4101.0)])
        assert sig is not None
        assert sig.signal_type == SignalType.FLAT
        assert sig.metadata["action"] == "close_all"
        assert sig.metadata["reason"] == "session_profit_target"

    def test_loss_limit_emits_close_all(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        strategy.update_unrealized_pnl(-60.0)  # below default -50 limit
        sig = strategy.generate([_bar(4099.0)])
        assert sig is not None
        assert sig.signal_type == SignalType.FLAT
        assert sig.metadata["reason"] == "session_loss_limit"

    def test_close_all_complete_restarts_session(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        strategy.update_unrealized_pnl(150.0)
        strategy.generate([_bar(4101.0)])  # triggers close-all
        strategy.notify_close_all_complete()
        assert strategy._session_count == 2
        assert strategy._anchor_price is None  # reset for fresh start

    def test_daily_loss_limit_stops_trading(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        # A large realized loss breaches the daily limit (default 200).
        strategy.report_trade_closed("4115.00_long", -250.0, "close_sl")
        assert strategy._session_active is False


class TestDisplayState:
    def test_state_shape(self) -> None:
        strategy = _new()
        _drain_all(strategy, [_bar(4100.0)])
        state = strategy.get_display_state()
        assert state is not None
        assert state["type"] == "paired_grid"
        assert state["anchor_price"] == 4100.0
        assert isinstance(state["grid_levels"], list)
        levels = state["grid_levels"]
        # Each level entry has buy/sell sub-dicts with status/fill/sl/units.
        for lv in levels:
            assert "buy" in lv
            assert "sell" in lv
            assert "status" in lv["buy"]
            assert "units" in lv["sell"]

    def test_no_state_before_anchor(self) -> None:
        strategy = _new()
        assert strategy.get_display_state() is None
