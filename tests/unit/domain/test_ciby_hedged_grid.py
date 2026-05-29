"""Unit tests for CibyHedgedGridStrategy."""

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


class TestInitialization:
    """Test strategy initialization and first signal generation."""

    def test_first_call_sets_anchor_returns_long_signal(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=15.0)
        bars = _bars([3000.0, 3000.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.metadata["pair_side"] == "long"
        assert strategy._anchor_price == 3000.0

    def test_second_call_returns_short_signal_from_queue(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=15.0)
        bars = _bars([3000.0, 3000.0])
        strategy.generate(bars)  # long
        signal = strategy.generate(bars)  # short from queue

        assert signal is not None
        assert signal.signal_type == SignalType.SHORT
        assert signal.metadata["pair_side"] == "short"

    def test_third_call_returns_none_no_crossing(self) -> None:
        strategy = CibyHedgedGridStrategy(grid_spacing=15.0)
        bars = _bars([3000.0, 3000.0])
        strategy.generate(bars)  # long
        strategy.generate(bars)  # short
        signal = strategy.generate(bars)  # no crossing

        assert signal is None

    def test_initial_pair_uses_initial_units(self) -> None:
        strategy = CibyHedgedGridStrategy(initial_units=10.0, grid_units=20.0)
        bars = _bars([3000.0, 3000.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.metadata["fixed_units"] == "10.0"

    def test_insufficient_bars_returns_none(self) -> None:
        strategy = CibyHedgedGridStrategy()
        signal = strategy.generate([_bar(3000.0)])
        assert signal is None


class TestGridCrossing:
    """Test grid level crossing detection and pair generation."""

    def _init_strategy(self, anchor: float = 3000.0) -> CibyHedgedGridStrategy:
        """Initialize strategy with anchor set."""
        strategy = CibyHedgedGridStrategy(grid_spacing=15.0, grid_units=20.0)
        bars = _bars([anchor, anchor])
        strategy.generate(bars)  # long (sets anchor)
        strategy.generate(bars)  # short (drain queue)
        return strategy

    def test_upward_crossing_generates_pair(self) -> None:
        strategy = self._init_strategy(3000.0)
        # Price crosses 3015 (first grid level above)
        bars = _bars([3000.0, 3010.0, 3016.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.metadata["fixed_units"] == "20.0"

    def test_downward_crossing_generates_pair(self) -> None:
        strategy = self._init_strategy(3000.0)
        # Price crosses 2985 (first grid level below)
        bars = _bars([3000.0, 2990.0, 2984.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG  # First of pair is always LONG
        assert "2985.00" in signal.metadata["grid_level"]

    def test_filled_level_not_retriggered(self) -> None:
        strategy = self._init_strategy(3000.0)
        # Cross 3015 upward
        bars = _bars([3000.0, 3010.0, 3016.0])
        strategy.generate(bars)  # long
        strategy.generate(bars)  # short (queue)

        # Price dips and crosses 3015 again
        bars2 = _bars([3016.0, 3010.0, 3016.0])
        signal = strategy.generate(bars2)

        # Should NOT trigger because level is still filled
        assert signal is None

    def test_level_released_after_both_sides_close(self) -> None:
        strategy = self._init_strategy(3000.0)
        # Cross 3015
        bars = _bars([3000.0, 3010.0, 3016.0])
        strategy.generate(bars)  # long
        strategy.generate(bars)  # short

        # Report both sides closed
        strategy.report_trade_closed("3015.00_long", 5.0)
        strategy.report_trade_closed("3015.00_short", -3.0)

        # Level should be free now — cross again
        bars2 = _bars([3010.0, 3010.0, 3016.0])
        signal = strategy.generate(bars2)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG

    def test_grid_level_keys_include_side_suffix(self) -> None:
        strategy = self._init_strategy(3000.0)
        bars = _bars([3000.0, 3010.0, 3016.0])
        long_signal = strategy.generate(bars)
        short_signal = strategy.generate(bars)

        assert long_signal is not None
        assert short_signal is not None
        assert long_signal.metadata["grid_level"].endswith("_long")
        assert short_signal.metadata["grid_level"].endswith("_short")


class TestSignalRejection:
    """Test on_signal_rejected clears queue and releases level."""

    def _init_strategy(self, anchor: float = 3000.0) -> CibyHedgedGridStrategy:
        strategy = CibyHedgedGridStrategy(grid_spacing=15.0, grid_units=20.0)
        bars = _bars([anchor, anchor])
        strategy.generate(bars)  # long (sets anchor)
        strategy.generate(bars)  # short (drain queue)
        return strategy

    def test_rejection_clears_queued_partner(self) -> None:
        strategy = self._init_strategy(3000.0)
        # Cross 3015 — generates LONG, queues SHORT
        bars = _bars([3000.0, 3010.0, 3016.0])
        long_signal = strategy.generate(bars)
        assert long_signal is not None

        # Simulate engine rejecting the LONG
        strategy.on_signal_rejected(long_signal.metadata["grid_level"])

        # Queue should be cleared — no SHORT will fire
        # Use price that doesn't cross any level to confirm queue is empty
        bars_flat = _bars([3016.0, 3016.0])
        signal = strategy.generate(bars_flat)
        assert signal is None

    def test_rejection_releases_filled_level(self) -> None:
        strategy = self._init_strategy(3000.0)
        bars = _bars([3000.0, 3010.0, 3016.0])
        long_signal = strategy.generate(bars)
        assert long_signal is not None

        # Level is filled
        assert 3015.0 in strategy._filled_levels

        # Reject the signal
        strategy.on_signal_rejected(long_signal.metadata["grid_level"])

        # Level should be free again
        assert 3015.0 not in strategy._filled_levels

    def test_released_level_can_retrigger(self) -> None:
        strategy = self._init_strategy(3000.0)
        bars = _bars([3000.0, 3010.0, 3016.0])
        long_signal = strategy.generate(bars)
        assert long_signal is not None

        strategy.on_signal_rejected(long_signal.metadata["grid_level"])

        # Cross 3015 again — should trigger fresh pair
        bars2 = _bars([3010.0, 3010.0, 3016.0])
        signal = strategy.generate(bars2)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG


class TestSessionPnlExits:
    """Test session profit target and loss limit exits."""

    def _init_strategy(
        self,
        session_profit_target: float = 100.0,
        session_loss_limit: float = 50.0,
    ) -> CibyHedgedGridStrategy:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            session_profit_target=session_profit_target,
            session_loss_limit=session_loss_limit,
        )
        bars = _bars([3000.0, 3000.0])
        strategy.generate(bars)  # long
        strategy.generate(bars)  # short
        return strategy

    def test_session_profit_target_triggers_close_all(self) -> None:
        strategy = self._init_strategy(session_profit_target=50.0)

        # Simulate profitable closures
        strategy.report_trade_closed("3000.00_long", 30.0)
        strategy.report_trade_closed("3000.00_short", 25.0)

        # Next generate should return FLAT close_all
        bars = _bars([3000.0, 3005.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.FLAT
        assert signal.metadata["action"] == "close_all"
        assert signal.metadata["reason"] == "session_profit_target"

    def test_session_loss_limit_triggers_close_all(self) -> None:
        strategy = self._init_strategy(session_loss_limit=30.0)

        # Simulate losses
        strategy.report_trade_closed("3000.00_long", -20.0)
        strategy.report_trade_closed("3000.00_short", -15.0)

        bars = _bars([3000.0, 3005.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.FLAT
        assert signal.metadata["action"] == "close_all"
        assert signal.metadata["reason"] == "session_loss_limit"

    def test_restart_resets_session_state(self) -> None:
        strategy = self._init_strategy(session_profit_target=50.0)
        strategy.report_trade_closed("3000.00_long", 55.0)

        # Trigger close_all
        bars = _bars([3000.0, 3005.0])
        strategy.generate(bars)  # FLAT close_all

        # Simulate engine calling notify_close_all_complete
        strategy.notify_close_all_complete()

        # Strategy should be reset — next generate starts fresh session
        assert strategy._anchor_price is None
        assert strategy._session_realized_pnl == 0.0
        assert strategy._session_count == 2

    def test_restart_preserves_daily_pnl(self) -> None:
        strategy = self._init_strategy(session_profit_target=50.0)
        strategy.report_trade_closed("3000.00_long", 55.0)

        bars = _bars([3000.0, 3005.0])
        strategy.generate(bars)  # FLAT
        strategy.notify_close_all_complete()

        assert strategy._daily_realized_pnl == 55.0


class TestDailyLossLimit:
    """Test daily loss limit behavior."""

    def test_daily_limit_stops_trading(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            daily_loss_limit=100.0,
            session_loss_limit=200.0,  # High so session limit doesn't hit first
        )
        bars = _bars([3000.0, 3000.0])
        strategy.generate(bars)  # long
        strategy.generate(bars)  # short

        # Simulate daily loss exceeding limit
        strategy.report_trade_closed("3000.00_long", -60.0)
        strategy.report_trade_closed("3000.00_short", -50.0)

        # Strategy should be inactive
        assert not strategy._session_active

        # Generate should return None (after close_all is handled)
        # First it will emit close_all
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.FLAT

        strategy.notify_close_all_complete()

        # Now it should return None
        signal = strategy.generate(bars)
        assert signal is None

    def test_day_boundary_resets_daily_pnl(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            daily_loss_limit=100.0,
            session_loss_limit=200.0,
        )
        # Day 1
        bars_day1 = _bars([3000.0, 3000.0], day="2025-05-01")
        strategy.generate(bars_day1)
        strategy.generate(bars_day1)
        strategy.report_trade_closed("3000.00_long", -60.0)
        strategy.report_trade_closed("3000.00_short", -50.0)

        # Daily limit hit
        assert not strategy._session_active

        # Handle the close_all
        strategy.generate(bars_day1)
        strategy.notify_close_all_complete()

        # Day 2 — should reset
        bars_day2 = _bars([3000.0, 3000.0], day="2025-05-02")
        signal = strategy.generate(bars_day2)

        assert strategy._session_active
        assert strategy._daily_realized_pnl == 0.0
        assert signal is not None  # New session starts


class TestDisplayState:
    """Test get_display_state output."""

    def test_returns_none_before_init(self) -> None:
        strategy = CibyHedgedGridStrategy()
        assert strategy.get_display_state() is None

    def test_returns_correct_structure(self) -> None:
        strategy = CibyHedgedGridStrategy(
            grid_spacing=15.0,
            session_profit_target=100.0,
            session_loss_limit=50.0,
            daily_loss_limit=200.0,
        )
        bars = _bars([3000.0, 3000.0])
        strategy.generate(bars)
        strategy.generate(bars)

        state = strategy.get_display_state()
        assert state is not None
        assert state["type"] == "paired_grid"
        assert state["anchor_price"] == 3000.0
        assert state["session_pnl"] == 0.0
        assert state["session_profit_target"] == 100.0
        assert state["session_loss_limit"] == 50.0
        assert state["daily_pnl"] == 0.0
        assert state["daily_loss_limit"] == 200.0
        assert state["session_count"] == 1
        assert state["session_active"] is True
        assert isinstance(state["grid_levels"], list)


class TestMetadata:
    """Test strategy metadata."""

    def test_metadata_has_all_params(self) -> None:
        meta = CibyHedgedGridStrategy.metadata()
        param_keys = {p.key for p in meta.params}
        expected = {
            "grid_spacing",
            "initial_units",
            "grid_units",
            "stop_distance",
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


class TestStopLoss:
    """Test stop-loss placement on signals."""

    def test_long_signal_stop_below_entry(self) -> None:
        strategy = CibyHedgedGridStrategy(stop_distance=16.0)
        bars = _bars([3000.0, 3000.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.stop_loss == 3000.0 - 16.0

    def test_short_signal_stop_above_entry(self) -> None:
        strategy = CibyHedgedGridStrategy(stop_distance=16.0)
        bars = _bars([3000.0, 3000.0])
        strategy.generate(bars)  # long
        signal = strategy.generate(bars)  # short

        assert signal is not None
        assert signal.stop_loss == 3000.0 + 16.0

    def test_no_take_profit(self) -> None:
        strategy = CibyHedgedGridStrategy()
        bars = _bars([3000.0, 3000.0])
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.take_profit is None
