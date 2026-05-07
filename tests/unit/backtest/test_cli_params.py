"""Tests for CLI parameter parsing helpers."""

import pytest

from aurex_trade.backtest.cli import _default_params, _parse_param_grid, _parse_params


class TestParseParams:
    """Tests for _parse_params (single-value param parsing for run subcommand)."""

    def test_single_int_param(self) -> None:
        result = _parse_params(["period=14"])
        assert result == {"period": 14}
        assert isinstance(result["period"], int)

    def test_multiple_int_params(self) -> None:
        result = _parse_params(["short_window=10", "long_window=30"])
        assert result == {"short_window": 10, "long_window": 30}

    def test_float_param(self) -> None:
        result = _parse_params(["atr_multiplier=2.5"])
        assert result == {"atr_multiplier": 2.5}
        assert isinstance(result["atr_multiplier"], float)

    def test_mixed_int_and_float(self) -> None:
        result = _parse_params(["period=14", "atr_multiplier=1.5"])
        assert result == {"period": 14, "atr_multiplier": 1.5}
        assert isinstance(result["period"], int)
        assert isinstance(result["atr_multiplier"], float)

    def test_invalid_format_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_params(["no_equals_sign"])

    def test_non_numeric_value_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_params(["period=abc"])


class TestParseParamGrid:
    """Tests for _parse_param_grid (multi-value param parsing for sweep)."""

    def test_single_param_int_values(self) -> None:
        result = _parse_param_grid(["period=7,14,21"])
        assert result == {"period": [7, 14, 21]}

    def test_multiple_params(self) -> None:
        result = _parse_param_grid(["period=7,14", "overbought=70,80"])
        assert result == {"period": [7, 14], "overbought": [70, 80]}

    def test_float_values(self) -> None:
        result = _parse_param_grid(["atr_multiplier=1.5,2.0,2.5"])
        assert result == {"atr_multiplier": [1.5, 2.0, 2.5]}

    def test_mixed_int_and_float(self) -> None:
        result = _parse_param_grid(["period=7,14", "atr_multiplier=1.5,2.0"])
        assert result["period"] == [7, 14]
        assert result["atr_multiplier"] == [1.5, 2.0]

    def test_invalid_format_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_param_grid(["no_equals"])

    def test_non_numeric_value_exits(self) -> None:
        with pytest.raises(SystemExit):
            _parse_param_grid(["period=abc,def"])


class TestDefaultParams:
    """Tests for _default_params (reading defaults from metadata)."""

    def test_sma_crossover_defaults(self) -> None:
        params = _default_params("sma_crossover")
        assert params["short_window"] == 10
        assert params["long_window"] == 30
        assert params["atr_multiplier"] == 2.0
        assert params["atr_period"] == 14

    def test_rsi_mean_reversion_defaults(self) -> None:
        params = _default_params("rsi_mean_reversion")
        assert params["period"] == 14
        assert params["overbought"] == 70
        assert params["oversold"] == 30
        assert params["atr_multiplier"] == 2.0
        assert params["atr_period"] == 14
