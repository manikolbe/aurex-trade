"""Tests for CLI parameter parsing helpers."""

import pytest

from aurex_trade.backtest.cli import _default_params, _parse_param_grid, _parse_params


class TestParseParams:
    """Tests for _parse_params (single-value param parsing for run subcommand)."""

    def test_single_int_param(self) -> None:
        result = _parse_params(["grid_spacing=14"])
        assert result == {"grid_spacing": 14}
        assert isinstance(result["grid_spacing"], int)

    def test_multiple_int_params(self) -> None:
        result = _parse_params(["grid_spacing=10", "anchor_gap=30"])
        assert result == {"grid_spacing": 10, "anchor_gap": 30}

    def test_float_param(self) -> None:
        result = _parse_params(["stop_buffer=2.5"])
        assert result == {"stop_buffer": 2.5}
        assert isinstance(result["stop_buffer"], float)

    def test_mixed_int_and_float(self) -> None:
        result = _parse_params(["grid_spacing=14", "stop_buffer=1.5"])
        assert result == {"grid_spacing": 14, "stop_buffer": 1.5}
        assert isinstance(result["grid_spacing"], int)
        assert isinstance(result["stop_buffer"], float)

    def test_invalid_format_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_params(["no_equals_sign"])

    def test_non_numeric_value_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_params(["grid_spacing=abc"])


class TestParseParamGrid:
    """Tests for _parse_param_grid (multi-value param parsing for sweep)."""

    def test_single_param_int_values(self) -> None:
        result = _parse_param_grid(["grid_spacing=7,14,21"])
        assert result == {"grid_spacing": [7, 14, 21]}

    def test_multiple_params(self) -> None:
        result = _parse_param_grid(["grid_spacing=7,14", "anchor_gap=70,80"])
        assert result == {"grid_spacing": [7, 14], "anchor_gap": [70, 80]}

    def test_float_values(self) -> None:
        result = _parse_param_grid(["stop_buffer=1.5,2.0,2.5"])
        assert result == {"stop_buffer": [1.5, 2.0, 2.5]}

    def test_mixed_int_and_float(self) -> None:
        result = _parse_param_grid(["grid_spacing=7,14", "stop_buffer=1.5,2.0"])
        assert result["grid_spacing"] == [7, 14]
        assert result["stop_buffer"] == [1.5, 2.0]

    def test_invalid_format_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_param_grid(["no_equals"])

    def test_non_numeric_value_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_param_grid(["grid_spacing=abc,def"])


class TestDefaultParams:
    """Tests for _default_params (reading defaults from metadata)."""

    def test_ciby_sliding_grid_defaults(self) -> None:
        params = _default_params("ciby_sliding_grid")
        assert params["grid_spacing"] == 10
        assert params["anchor_gap"] == 15
        assert params["grid_units"] == 20
        assert params["session_profit_target"] == 100

    def test_ciby_hedged_doubling_grid_defaults(self) -> None:
        params = _default_params("ciby_hedged_doubling_grid")
        assert params["spacing"] == 20
        assert params["units"] == 2
        assert params["trailing_stop_distance"] == 20
        assert params["whipsaw_limit"] == 3
