"""Unit tests for CibyHedgedGridStrategy — limit order mode."""

from datetime import datetime

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData
from aurex_trade.domain.strategy.ciby_hedged_grid import CibyHedgedGridStrategy


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


def _bars(prices: list[float], day: str = "2025-05-01") -> list[BarData]:
    """Create a list of BarData from prices."""
    return [_bar(p, day=day) for p in prices]


def _drain_all(strategy: CibyHedgedGridStrategy, bars: list[BarData]) -> list[object]:
    """Drain all signals from the strategy's queue."""
    signals = []
    while True:
        sig = strategy.generate(bars)
        if sig is None:
            break
        signals.append(sig)
    return signals


class TestInitialization:
    """Test strategy initialization and limit order placement."""

    def test_first_call_sets_anchor_and_queues_signals(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        signal = strategy.generate(bars)

        assert signal is not None
        assert strategy._anchor_price == 4563.0

    def test_grid_levels_are_rounded_multiples(self) -> None:
        """grid_spacing=10, price=4563 → levels at 4550, 4560, 4570, 4580."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]

        # Drain all signals
        signals = _drain_all(strategy, bars)

        # Should have 2 levels above (4570, 4580) + 2 below (4560, 4550) = 4 levels
        # One signal per level (single-side limit)
        assert len(signals) == 4

        # Extract limit prices from signals
        limit_prices = sorted({float(s.metadata["limit_price"]) for s in signals})
        assert limit_prices == [4550.0, 4560.0, 4570.0, 4580.0]

    def test_all_signals_are_limit_orders(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        signals = _drain_all(strategy, bars)

        for sig in signals:
            assert sig.metadata["order_type"] == "LIMIT"
            assert "limit_price" in sig.metadata

    def test_correct_side_per_level(self) -> None:
        """Above price → sell limit, below price → buy limit."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]  # price = 4563
        signals = _drain_all(strategy, bars)

        for sig in signals:
            level = float(sig.metadata["limit_price"])
            if level > 4563.0:
                # Above price → sell limit (waits for rise)
                assert sig.signal_type == SignalType.SHORT
                assert sig.metadata["pair_side"] == "short"
                assert sig.metadata["opposite_side"] == "BUY"
            else:
                # Below price → buy limit (waits for drop)
                assert sig.signal_type == SignalType.LONG
                assert sig.metadata["pair_side"] == "long"
                assert sig.metadata["opposite_side"] == "SELL"

    def test_units_match_grid_units(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0, grid_units=15.0)
        bars = [_bar(4563.0)]
        signals = _drain_all(strategy, bars)

        for sig in signals:
            assert sig.metadata["fixed_units"] == "15.0"

    def test_no_signal_on_empty_bars(self) -> None:
        strategy = CibyHedgedGridStrategy()
        signal = strategy.generate([])
        assert signal is None

    def test_subsequent_call_returns_none_after_drain(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        # No more signals
        signal = strategy.generate(bars)
        assert signal is None


class TestGridLevelRounding:
    """Test grid level calculation with various prices and spacings."""

    def test_price_exactly_on_grid_line(self) -> None:
        """Price=4560, spacing=10 → levels 4540, 4550, 4570, 4580 (skip 4560)."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4560.0)]
        signals = _drain_all(strategy, bars)

        limit_prices = sorted({float(s.metadata["limit_price"]) for s in signals})
        # On grid line: skip current price, place 2 above + 2 below
        assert limit_prices == [4540.0, 4550.0, 4570.0, 4580.0]
        assert 4560.0 not in limit_prices  # Never place at current price

    def test_spacing_5(self) -> None:
        """Price=4563, spacing=5 → levels 4555, 4560, 4565, 4570."""
        strategy = CibyHedgedGridStrategy(grid_spacing=5.0)
        bars = [_bar(4563.0)]
        signals = _drain_all(strategy, bars)

        limit_prices = sorted({float(s.metadata["limit_price"]) for s in signals})
        assert limit_prices == [4555.0, 4560.0, 4565.0, 4570.0]


class TestStopLoss:
    """Test stop-loss placement on limit order signals."""

    def test_long_signal_stop_below_level(self) -> None:
        """Buy stop = level - grid_spacing."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        signals = _drain_all(strategy, bars)

        long_signals = [s for s in signals if s.signal_type == SignalType.LONG]
        for sig in long_signals:
            level = float(sig.metadata["limit_price"])
            assert sig.stop_loss == level - 10.0

    def test_short_signal_stop_above_level(self) -> None:
        """Sell stop = level + grid_spacing."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        signals = _drain_all(strategy, bars)

        short_signals = [s for s in signals if s.signal_type == SignalType.SHORT]
        for sig in short_signals:
            level = float(sig.metadata["limit_price"])
            assert sig.stop_loss == level + 10.0

    def test_no_take_profit(self) -> None:
        strategy = CibyHedgedGridStrategy()
        bars = [_bar(4563.0)]
        signals = _drain_all(strategy, bars)

        for sig in signals:
            assert sig.take_profit is None


class TestReplenishment:
    """Test that filling a level triggers next level placement."""

    def test_fill_above_anchor_places_next_above(self) -> None:
        """Filling level 4580 (above anchor=4563) → place 4590."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        # 4580 should already be placed (2 levels above)
        # Simulate fill at 4580 — should place 4590
        strategy.report_fill("4580.00_short", 4580.0)

        # Drain new signals
        signals = _drain_all(strategy, bars)
        assert len(signals) == 1  # single-side for 4590

        limit_prices = {float(s.metadata["limit_price"]) for s in signals}
        assert 4590.0 in limit_prices

    def test_fill_below_anchor_places_next_below(self) -> None:
        """Filling level 4550 (below anchor=4563) → place 4540."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        # 4550 should be placed (2 levels below)
        # Simulate fill at 4550 — should place 4540
        strategy.report_fill("4550.00_long", 4550.0)

        signals = _drain_all(strategy, bars)
        assert len(signals) == 1

        limit_prices = {float(s.metadata["limit_price"]) for s in signals}
        assert 4540.0 in limit_prices

    def test_no_duplicate_placement(self) -> None:
        """Filling a level that already has the next level placed does nothing extra."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        # Fill 4570 — next would be 4580 but it's already placed
        strategy.report_fill("4570.00_short", 4570.0)

        signals = _drain_all(strategy, bars)
        # 4580 already placed, so no new signals
        assert len(signals) == 0


class TestSignalRejection:
    """Test on_signal_rejected clears queue and releases level."""

    def test_rejection_clears_from_placed_levels(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]

        # Get first signal
        first_signal = strategy.generate(bars)
        assert first_signal is not None
        level = float(first_signal.metadata["limit_price"])

        # Reject it — should remove from placed
        strategy.on_signal_rejected(first_signal.metadata["grid_level"])
        assert level not in strategy._placed_levels

    def test_rejected_level_is_re_placed_by_maintenance(self) -> None:
        """Cancelled/rejected levels get re-placed on the next generate cycle."""
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        # Reject a level
        strategy.on_signal_rejected("4570.00_short")
        assert 4570.0 not in strategy._placed_levels

        # Next generate triggers maintenance → re-places 4570
        signal = strategy.generate(bars)
        assert signal is not None
        assert float(signal.metadata["limit_price"]) == 4570.0

    def test_rejection_releases_placed_level(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]

        long_signal = strategy.generate(bars)
        assert long_signal is not None

        level = float(long_signal.metadata["limit_price"])
        assert level in strategy._placed_levels

        strategy.on_signal_rejected(long_signal.metadata["grid_level"])
        assert level not in strategy._placed_levels


class TestSessionPnlExits:
    """Test session profit target and loss limit exits."""

    def _init_strategy(
        self,
        session_profit_target: float = 100.0,
        session_loss_limit: float = 50.0,
    ) -> CibyHedgedGridStrategy:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=10.0,
            session_profit_target=session_profit_target,
            session_loss_limit=session_loss_limit,
        )
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)
        return strategy

    def test_session_profit_target_triggers_close_all(self) -> None:
        strategy = self._init_strategy(session_profit_target=50.0)

        # Simulate profitable closures
        strategy.report_trade_closed("4570.00_long", 30.0)
        strategy.report_trade_closed("4570.00_short", 25.0)

        # Next generate should return FLAT close_all
        bars = [_bar(4565.0)]
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.FLAT
        assert signal.metadata["action"] == "close_all"
        assert signal.metadata["reason"] == "session_profit_target"

    def test_session_loss_limit_triggers_close_all(self) -> None:
        strategy = self._init_strategy(session_loss_limit=30.0)

        # Simulate losses
        strategy.report_trade_closed("4570.00_long", -20.0)
        strategy.report_trade_closed("4570.00_short", -15.0)

        bars = [_bar(4565.0)]
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.FLAT
        assert signal.metadata["action"] == "close_all"
        assert signal.metadata["reason"] == "session_loss_limit"

    def test_restart_resets_session_state(self) -> None:
        strategy = self._init_strategy(session_profit_target=50.0)
        strategy.report_trade_closed("4570.00_long", 55.0)

        bars = [_bar(4565.0)]
        strategy.generate(bars)  # FLAT close_all
        strategy.notify_close_all_complete()

        assert strategy._anchor_price is None
        assert strategy._session_realized_pnl == 0.0
        assert strategy._session_count == 2

    def test_restart_preserves_daily_pnl(self) -> None:
        strategy = self._init_strategy(session_profit_target=50.0)
        strategy.report_trade_closed("4570.00_long", 55.0)

        bars = [_bar(4565.0)]
        strategy.generate(bars)  # FLAT
        strategy.notify_close_all_complete()

        assert strategy._daily_realized_pnl == 55.0


class TestDailyLossLimit:
    """Test daily loss limit behavior."""

    def test_daily_limit_stops_trading(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=10.0,
            daily_loss_limit=100.0,
            session_loss_limit=200.0,
        )
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        # Simulate daily loss exceeding limit
        strategy.report_trade_closed("4570.00_long", -60.0)
        strategy.report_trade_closed("4570.00_short", -50.0)

        assert not strategy._session_active

        # Generate should return FLAT (close_all pending)
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.FLAT

        strategy.notify_close_all_complete()

        # Now it should return None
        signal = strategy.generate(bars)
        assert signal is None

    def test_day_boundary_resets_daily_pnl(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=10.0,
            daily_loss_limit=100.0,
            session_loss_limit=200.0,
        )
        # Day 1
        bars_day1 = [_bar(4563.0, day="2025-05-01")]
        _drain_all(strategy, bars_day1)
        strategy.report_trade_closed("4570.00_long", -60.0)
        strategy.report_trade_closed("4570.00_short", -50.0)

        assert not strategy._session_active

        # Handle close_all
        strategy.generate(bars_day1)
        strategy.notify_close_all_complete()

        # Day 2 — should reset
        bars_day2 = [_bar(4563.0, day="2025-05-02")]
        signal = strategy.generate(bars_day2)

        assert strategy._session_active
        assert strategy._daily_realized_pnl == 0.0
        assert signal is not None


class TestDisplayState:
    """Test get_display_state output."""

    def test_returns_none_before_init(self) -> None:
        strategy = CibyHedgedGridStrategy()
        assert strategy.get_display_state() is None

    def test_returns_correct_structure(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=10.0,
            session_profit_target=100.0,
            session_loss_limit=50.0,
            daily_loss_limit=200.0,
        )
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        state = strategy.get_display_state()
        assert state is not None
        assert state["type"] == "paired_grid"
        assert state["anchor_price"] == 4563.0
        assert state["session_pnl"] == 0.0
        assert state["session_profit_target"] == 100.0
        assert state["session_loss_limit"] == 50.0
        assert state["daily_pnl"] == 0.0
        assert state["daily_loss_limit"] == 200.0
        assert state["session_count"] == 1
        assert state["session_active"] is True
        assert isinstance(state["grid_levels"], list)
        assert state["placed_count"] == 4  # 2 above + 2 below

    def test_placed_levels_show_placed_status(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=10.0)
        bars = [_bar(4563.0)]
        _drain_all(strategy, bars)

        state = strategy.get_display_state()
        assert state is not None
        grid_levels = state["grid_levels"]
        placed = [lv for lv in grid_levels if lv["status"] == "placed"]
        assert len(placed) == 4


class TestMetadata:
    """Test strategy metadata."""

    def test_metadata_has_all_params(self) -> None:
        meta = CibyHedgedGridStrategy.metadata()
        param_keys = {p.key for p in meta.params}
        expected = {
            "grid_spacing",
            "grid_units",
            "session_profit_target",
            "session_loss_limit",
            "daily_loss_limit",
        }
        assert param_keys == expected

    def test_metadata_display_name(self) -> None:
        meta = CibyHedgedGridStrategy.metadata()
        assert meta.display_name == "Ciby Hedged Grid"

    def test_name_property(self) -> None:
        strategy = CibyHedgedGridStrategy()
        assert strategy.name == "ciby_hedged_grid"
