"""Tests for strategy metadata Protocol extension."""

import pytest

from aurex_trade.backtest.cli import get_strategy_metadata
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata
from aurex_trade.domain.strategy.sma_crossover import SMACrossover


class TestParamMeta:
    def test_frozen(self) -> None:
        param = ParamMeta(
            key="x", label="X", tooltip="tip", default=5, min_value=1, max_value=10
        )
        assert param.key == "x"
        # Frozen — assignment should raise
        try:
            param.key = "y"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass


class TestStrategyMetadata:
    def test_frozen(self) -> None:
        meta = StrategyMetadata(
            display_name="Test", description="Desc", params=()
        )
        try:
            meta.display_name = "Other"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass


class TestSMACrossoverMetadata:
    def test_returns_strategy_metadata(self) -> None:
        meta = SMACrossover.metadata()
        assert isinstance(meta, StrategyMetadata)

    def test_display_name(self) -> None:
        meta = SMACrossover.metadata()
        assert meta.display_name == "SMA Crossover"

    def test_description_is_nonempty(self) -> None:
        meta = SMACrossover.metadata()
        assert len(meta.description) > 50

    def test_params_are_tuple_of_param_meta(self) -> None:
        meta = SMACrossover.metadata()
        assert isinstance(meta.params, tuple)
        assert len(meta.params) == 4
        for p in meta.params:
            assert isinstance(p, ParamMeta)

    def test_param_keys(self) -> None:
        meta = SMACrossover.metadata()
        keys = [p.key for p in meta.params]
        assert keys == ["short_window", "long_window", "atr_multiplier", "atr_period"]

    def test_param_ranges_valid(self) -> None:
        meta = SMACrossover.metadata()
        for p in meta.params:
            assert p.min_value < p.max_value
            assert p.min_value <= p.default <= p.max_value

    def test_callable_on_instance_too(self) -> None:
        strategy = SMACrossover(short_window=10, long_window=30)
        meta = strategy.metadata()
        assert meta.display_name == "SMA Crossover"


class TestGetStrategyMetadata:
    def test_known_strategy(self) -> None:
        meta = get_strategy_metadata("sma_crossover")
        assert meta.display_name == "SMA Crossover"

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(KeyError):
            get_strategy_metadata("nonexistent")
