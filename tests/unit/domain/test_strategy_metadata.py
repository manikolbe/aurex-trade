"""Tests for strategy metadata Protocol extension."""

import pytest

from aurex_trade.backtest.cli import get_strategy_metadata
from aurex_trade.domain.strategy.base import ParamMeta, StrategyMetadata
from aurex_trade.domain.strategy.ciby_hedged_doubling_grid import CibyHedgedDoublingGridStrategy
from aurex_trade.domain.strategy.ciby_sliding_grid import CibySlidingGridStrategy


class TestParamMeta:
    def test_frozen(self) -> None:
        param = ParamMeta(key="x", label="X", tooltip="tip", default=5, min_value=1, max_value=10)
        assert param.key == "x"
        # Frozen — assignment should raise
        try:
            param.key = "y"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass


class TestStrategyMetadata:
    def test_frozen(self) -> None:
        meta = StrategyMetadata(display_name="Test", description="Desc", params=())
        try:
            meta.display_name = "Other"  # type: ignore[misc]
            raise AssertionError("Should have raised FrozenInstanceError")
        except AttributeError:
            pass


class TestCibySlidingGridMetadata:
    def test_returns_strategy_metadata(self) -> None:
        meta = CibySlidingGridStrategy.metadata()
        assert isinstance(meta, StrategyMetadata)

    def test_display_name(self) -> None:
        meta = CibySlidingGridStrategy.metadata()
        assert meta.display_name == "Ciby Sliding Grid"

    def test_description_is_nonempty(self) -> None:
        meta = CibySlidingGridStrategy.metadata()
        assert len(meta.description) > 50

    def test_params_are_tuple_of_param_meta(self) -> None:
        meta = CibySlidingGridStrategy.metadata()
        assert isinstance(meta.params, tuple)
        assert len(meta.params) == 11
        for p in meta.params:
            assert isinstance(p, ParamMeta)

    def test_param_keys(self) -> None:
        meta = CibySlidingGridStrategy.metadata()
        keys = [p.key for p in meta.params]
        assert keys == [
            "grid_spacing",
            "anchor_gap",
            "buy_sell_offset",
            "anchor_units",
            "grid_units",
            "stop_buffer",
            "max_levels_ahead",
            "max_levels_behind",
            "session_profit_target",
            "session_loss_limit",
            "daily_loss_limit",
        ]

    def test_param_ranges_valid(self) -> None:
        meta = CibySlidingGridStrategy.metadata()
        for p in meta.params:
            assert p.min_value < p.max_value
            assert p.min_value <= p.default <= p.max_value

    def test_callable_on_instance_too(self) -> None:
        strategy = CibySlidingGridStrategy()
        meta = strategy.metadata()
        assert meta.display_name == "Ciby Sliding Grid"


class TestCibyHedgedDoublingGridMetadata:
    def test_returns_strategy_metadata(self) -> None:
        meta = CibyHedgedDoublingGridStrategy.metadata()
        assert isinstance(meta, StrategyMetadata)

    def test_display_name(self) -> None:
        meta = CibyHedgedDoublingGridStrategy.metadata()
        assert meta.display_name == "Ciby Hedged Doubling Grid"

    def test_description_is_nonempty(self) -> None:
        meta = CibyHedgedDoublingGridStrategy.metadata()
        assert len(meta.description) > 50

    def test_params_are_tuple_of_param_meta(self) -> None:
        meta = CibyHedgedDoublingGridStrategy.metadata()
        assert isinstance(meta.params, tuple)
        assert len(meta.params) == 5
        for p in meta.params:
            assert isinstance(p, ParamMeta)

    def test_param_keys(self) -> None:
        meta = CibyHedgedDoublingGridStrategy.metadata()
        keys = [p.key for p in meta.params]
        assert keys == [
            "spacing",
            "units",
            "trailing_stop_distance",
            "session_loss_limit",
            "whipsaw_limit",
        ]

    def test_param_ranges_valid(self) -> None:
        meta = CibyHedgedDoublingGridStrategy.metadata()
        for p in meta.params:
            assert p.min_value < p.max_value
            assert p.min_value <= p.default <= p.max_value

    def test_callable_on_instance_too(self) -> None:
        strategy = CibyHedgedDoublingGridStrategy()
        meta = strategy.metadata()
        assert meta.display_name == "Ciby Hedged Doubling Grid"


class TestGetStrategyMetadata:
    def test_known_strategy_sliding_grid(self) -> None:
        meta = get_strategy_metadata("ciby_sliding_grid")
        assert meta.display_name == "Ciby Sliding Grid"

    def test_known_strategy_doubling_grid(self) -> None:
        meta = get_strategy_metadata("ciby_hedged_doubling_grid")
        assert meta.display_name == "Ciby Hedged Doubling Grid"

    def test_unknown_strategy_raises(self) -> None:
        with pytest.raises(KeyError):
            get_strategy_metadata("nonexistent")
