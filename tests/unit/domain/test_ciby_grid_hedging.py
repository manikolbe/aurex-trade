"""Tests for the Ciby Grid Hedging strategy."""

from datetime import UTC, datetime, timedelta

from aurex_trade.domain.enums import SignalType
from aurex_trade.domain.models import BarData
from aurex_trade.domain.strategy.ciby_grid_hedging import CibyGridHedgingStrategy


def _make_bars(closes: list[float], symbol: str = "XAU_USD") -> list[BarData]:
    """Build a list of BarData from close prices."""
    return [
        BarData(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
            open=c,
            high=c + 1.0,
            low=c - 1.0,
            close=c,
            volume=100.0,
            symbol=symbol,
        )
        for i, c in enumerate(closes)
    ]


class TestCibyGridHedgingBasics:
    """Basic property and initialization tests."""

    def setup_method(self) -> None:
        self.strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0,
            max_levels=6,
            stop_distance=30.0,
            num_levels_above=3,
            num_levels_below=3,
        )

    def test_name(self) -> None:
        assert self.strategy.name == "ciby_grid_hedging"

    def test_min_bars(self) -> None:
        assert self.strategy.min_bars == 2

    def test_insufficient_data_returns_none(self) -> None:
        """Need at least 2 bars; 1 bar should return None."""
        bars = _make_bars([4590.0])
        assert self.strategy.generate(bars) is None

    def test_empty_bars_returns_none(self) -> None:
        assert self.strategy.generate([]) is None

    def test_first_call_initializes_grid_returns_none(self) -> None:
        """First valid call sets anchor and returns None (no signal on setup)."""
        bars = _make_bars([4590.0, 4591.0])
        assert self.strategy.generate(bars) is None

    def test_metadata_classmethod(self) -> None:
        """metadata() returns valid StrategyMetadata with all params."""
        meta = CibyGridHedgingStrategy.metadata()
        assert meta.display_name == "Ciby Grid Hedging"
        assert len(meta.params) == 6
        param_keys = [p.key for p in meta.params]
        assert "grid_spacing" in param_keys
        assert "max_levels" in param_keys
        assert "stop_distance" in param_keys
        assert "num_levels_above" in param_keys
        assert "num_levels_below" in param_keys


class TestCibyGridHedgingSignals:
    """Signal generation tests."""

    def setup_method(self) -> None:
        self.strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0,
            max_levels=6,
            stop_distance=30.0,
            num_levels_above=3,
            num_levels_below=3,
        )

    def _initialize_grid(self, anchor: float) -> None:
        """Helper to initialize the grid at a given anchor price."""
        bars = _make_bars([anchor, anchor])
        self.strategy.generate(bars)

    def test_upward_crossing_generates_long(self) -> None:
        """Price crossing upward through a grid level → LONG signal."""
        # Anchor at 4590 → levels at 4560, 4570, 4580, 4600, 4610, 4620
        self._initialize_grid(4590.0)

        # Price moves from 4599 to 4601 → crosses 4600 level upward
        bars = _make_bars([4599.0, 4601.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.symbol == "XAU_USD"
        assert signal.strategy_name == "ciby_grid_hedging"

    def test_downward_crossing_generates_short(self) -> None:
        """Price crossing downward through a grid level → SHORT signal."""
        self._initialize_grid(4590.0)

        # Price moves from 4581 to 4579 → crosses 4580 level downward
        bars = _make_bars([4581.0, 4579.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.SHORT

    def test_no_crossing_returns_none(self) -> None:
        """Price moves within a grid cell → None."""
        self._initialize_grid(4590.0)

        # Price moves from 4592 to 4595 — no level crossed
        bars = _make_bars([4592.0, 4595.0])
        signal = self.strategy.generate(bars)

        assert signal is None

    def test_max_levels_caps_signals(self) -> None:
        """Once max_levels are filled, no more signals are generated."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0,
            max_levels=2,
            stop_distance=30.0,
            num_levels_above=3,
            num_levels_below=3,
        )
        # Initialize at 4590
        bars = _make_bars([4590.0, 4590.0])
        strategy.generate(bars)

        # Fill level 1: cross 4600 upward
        bars = _make_bars([4599.0, 4601.0])
        signal = strategy.generate(bars)
        assert signal is not None

        # Fill level 2: cross 4610 upward
        bars = _make_bars([4609.0, 4611.0])
        signal = strategy.generate(bars)
        assert signal is not None

        # Level 3: should be capped
        bars = _make_bars([4619.0, 4621.0])
        signal = strategy.generate(bars)
        assert signal is None

    def test_same_level_not_refilled(self) -> None:
        """A level that's been triggered cannot be triggered again."""
        self._initialize_grid(4590.0)

        # Cross 4600 upward — fills the level
        bars = _make_bars([4599.0, 4601.0])
        signal = self.strategy.generate(bars)
        assert signal is not None

        # Price drops back below and crosses 4600 again upward
        bars = _make_bars([4599.0, 4601.0])
        signal = self.strategy.generate(bars)
        assert signal is None

    def test_multiple_levels_crossed_only_one_signal(self) -> None:
        """A gap crossing multiple levels only generates one signal per call."""
        self._initialize_grid(4590.0)

        # Price jumps from 4589 to 4615 — crosses 4600 and 4610
        bars = _make_bars([4589.0, 4615.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        # Should get the first (lowest) unfilled level crossed
        assert signal.metadata["grid_level"] == "4600.00"

        # Next call should pick up the second level
        bars = _make_bars([4589.0, 4615.0])
        signal = self.strategy.generate(bars)
        assert signal is not None
        assert signal.metadata["grid_level"] == "4610.00"


class TestCibyGridHedgingStopLoss:
    """Stop-loss calculation tests."""

    def setup_method(self) -> None:
        self.strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0,
            max_levels=6,
            stop_distance=25.0,
            num_levels_above=3,
            num_levels_below=3,
        )

    def _initialize_grid(self, anchor: float) -> None:
        bars = _make_bars([anchor, anchor])
        self.strategy.generate(bars)

    def test_long_signal_stop_below_entry(self) -> None:
        """LONG signal stop-loss should be below entry by stop_distance."""
        self._initialize_grid(4590.0)

        bars = _make_bars([4599.0, 4601.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.stop_loss is not None
        assert signal.stop_loss == 4601.0 - 25.0

    def test_short_signal_stop_above_entry(self) -> None:
        """SHORT signal stop-loss should be above entry by stop_distance."""
        self._initialize_grid(4590.0)

        bars = _make_bars([4581.0, 4579.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.SHORT
        assert signal.stop_loss is not None
        assert signal.stop_loss == 4579.0 + 25.0


class TestCibyGridHedgingMetadata:
    """Signal metadata content tests."""

    def setup_method(self) -> None:
        self.strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0,
            max_levels=6,
            stop_distance=30.0,
            num_levels_above=3,
            num_levels_below=3,
        )

    def test_signal_metadata_includes_grid_info(self) -> None:
        """Signal metadata should include grid-specific info."""
        bars = _make_bars([4590.0, 4590.0])
        self.strategy.generate(bars)

        bars = _make_bars([4599.0, 4601.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        assert "grid_level" in signal.metadata
        assert "anchor_price" in signal.metadata
        assert "filled_count" in signal.metadata
        assert "max_levels" in signal.metadata
        assert "entry_price" in signal.metadata
        assert signal.metadata["anchor_price"] == "4590.00"
        assert signal.metadata["filled_count"] == "1"

    def test_signal_strength_is_one(self) -> None:
        """Grid signals are binary — always full strength."""
        bars = _make_bars([4590.0, 4590.0])
        self.strategy.generate(bars)

        bars = _make_bars([4599.0, 4601.0])
        signal = self.strategy.generate(bars)

        assert signal is not None
        assert signal.strength == 1.0


class TestCibyGridHedgingParameterVariations:
    """Tests with different parameter configurations."""

    def test_custom_grid_spacing(self) -> None:
        """Grid levels respect custom spacing."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=5.0,
            max_levels=4,
            stop_distance=15.0,
            num_levels_above=2,
            num_levels_below=2,
        )
        # Initialize at 100
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)

        # Cross 105 upward (first level above with spacing=5)
        bars = _make_bars([104.0, 106.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.metadata["grid_level"] == "105.00"

    def test_asymmetric_levels(self) -> None:
        """Can have different number of levels above vs below."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0,
            max_levels=6,
            stop_distance=30.0,
            num_levels_above=1,
            num_levels_below=4,
        )
        # Initialize at 4600
        bars = _make_bars([4600.0, 4600.0])
        strategy.generate(bars)

        # Cross 4610 upward (only 1 level above)
        bars = _make_bars([4609.0, 4611.0])
        signal = strategy.generate(bars)
        assert signal is not None

        # No more levels above — 4620 doesn't exist
        bars = _make_bars([4619.0, 4621.0])
        signal = strategy.generate(bars)
        assert signal is None


class TestCibyGridHedgingTakeProfit:
    """Tests for take-profit calculation on grid signals."""

    def test_long_signal_take_profit_above_entry(self) -> None:
        """LONG signal TP should be above entry by reward_ratio * stop_distance."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0, stop_distance=30.0, reward_ratio=1.0,
            num_levels_above=3, num_levels_below=3,
        )
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)  # Initialize grid

        bars = _make_bars([109.0, 111.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.LONG
        assert signal.take_profit is not None
        entry = float(signal.metadata["entry_price"])
        assert signal.take_profit == entry + (1.0 * 30.0)

    def test_short_signal_take_profit_below_entry(self) -> None:
        """SHORT signal TP should be below entry by reward_ratio * stop_distance."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0, stop_distance=30.0, reward_ratio=1.0,
            num_levels_above=3, num_levels_below=3,
        )
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)  # Initialize grid

        bars = _make_bars([91.0, 89.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.signal_type == SignalType.SHORT
        assert signal.take_profit is not None
        entry = float(signal.metadata["entry_price"])
        assert signal.take_profit == entry - (1.0 * 30.0)

    def test_reward_ratio_zero_disables_take_profit(self) -> None:
        """reward_ratio=0 means no take-profit."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0, stop_distance=30.0, reward_ratio=0.0,
            num_levels_above=3, num_levels_below=3,
        )
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)

        bars = _make_bars([109.0, 111.0])
        signal = strategy.generate(bars)

        assert signal is not None
        assert signal.take_profit is None


class TestCibyGridHedgingReleaseLevel:
    """Tests for releasing triggered grid levels back to waiting."""

    def test_release_triggered_level_returns_true(self) -> None:
        """Releasing a triggered level should return True and free it."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0, stop_distance=30.0, reward_ratio=1.0,
            num_levels_above=3, num_levels_below=3,
        )
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)  # Initialize grid at anchor 100

        # Trigger a level by crossing it
        bars = _make_bars([109.0, 111.0])
        signal = strategy.generate(bars)
        assert signal is not None

        # Level 110.0 should now be triggered
        state = strategy.get_display_state()
        assert state is not None
        levels = state["levels"]
        assert isinstance(levels, list)
        triggered = [lv for lv in levels if lv["status"] == "triggered"]
        assert len(triggered) == 1

        # Release it
        assert strategy.release_level(110.0) is True

        # Now it should be back to waiting
        state = strategy.get_display_state()
        assert state is not None
        levels = state["levels"]
        assert isinstance(levels, list)
        triggered = [lv for lv in levels if lv["status"] == "triggered"]
        assert len(triggered) == 0

    def test_release_non_triggered_level_returns_false(self) -> None:
        """Releasing a level that isn't triggered should return False."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0, stop_distance=30.0, reward_ratio=1.0,
            num_levels_above=3, num_levels_below=3,
        )
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)  # Initialize grid

        assert strategy.release_level(110.0) is False

    def test_released_level_can_be_retriggered(self) -> None:
        """After release, a level should be available for re-entry."""
        strategy = CibyGridHedgingStrategy(
            grid_spacing=10.0, stop_distance=30.0, reward_ratio=1.0,
            num_levels_above=3, num_levels_below=3,
        )
        bars = _make_bars([100.0, 100.0])
        strategy.generate(bars)  # Initialize grid at anchor 100

        # Trigger level 110
        bars = _make_bars([109.0, 111.0])
        signal = strategy.generate(bars)
        assert signal is not None

        # Release it
        strategy.release_level(110.0)

        # Cross it again — should trigger again
        bars = _make_bars([109.0, 111.0])
        signal = strategy.generate(bars)
        assert signal is not None
        assert signal.signal_type == SignalType.LONG
