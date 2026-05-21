"""Tests for the OANDA market data adapter."""

from datetime import UTC
from unittest.mock import MagicMock

from aurex_trade.adapters.oanda.connection import OANDAAPIError, OANDAConnection
from aurex_trade.adapters.oanda.market_data import OANDAMarketDataAdapter


def _make_candles_response(count: int = 3, incomplete: bool = False) -> dict:  # type: ignore[type-arg]
    """Build a realistic OANDA candles API response."""
    candles = []
    for i in range(count):
        candles.append(
            {
                "time": f"2024-01-15T14:{30 + i:02d}:00.000000000Z",
                "bid": {
                    "o": str(2050.0 + i),
                    "h": str(2052.0 + i),
                    "l": str(2048.0 + i),
                    "c": str(2051.0 + i),
                },
                "volume": 100 + i * 10,
                "complete": True,
            }
        )
    if incomplete:
        candles.append(
            {
                "time": "2024-01-15T14:35:00.000000000Z",
                "bid": {"o": "2060.0", "h": "2062.0", "l": "2058.0", "c": "2061.0"},
                "volume": 50,
                "complete": False,
            }
        )
    return {"instrument": "XAU_USD", "granularity": "M1", "candles": candles}


class TestGetLatestBars:
    def setup_method(self) -> None:
        self.conn = MagicMock(spec=OANDAConnection)
        self.adapter = OANDAMarketDataAdapter(connection=self.conn, account_id="101-001-123")

    def test_returns_correct_count(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=5)
        bars = self.adapter.get_latest_bars("XAU_USD", 5)
        assert len(bars) == 5

    def test_bars_have_correct_symbol(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=2)
        bars = self.adapter.get_latest_bars("XAU_USD", 2)
        assert all(b.symbol == "XAU_USD" for b in bars)

    def test_bars_use_bid_prices(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=1)
        bars = self.adapter.get_latest_bars("XAU_USD", 1)
        bar = bars[0]
        assert bar.open == 2050.0
        assert bar.high == 2052.0
        assert bar.low == 2048.0
        assert bar.close == 2051.0

    def test_bars_have_utc_timestamps(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=2)
        bars = self.adapter.get_latest_bars("XAU_USD", 2)
        for bar in bars:
            assert bar.timestamp.tzinfo is not None
            assert bar.timestamp.tzinfo == UTC

    def test_bars_sorted_ascending(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=3)
        bars = self.adapter.get_latest_bars("XAU_USD", 3)
        timestamps = [b.timestamp for b in bars]
        assert timestamps == sorted(timestamps)

    def test_incomplete_candles_filtered(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=3, incomplete=True)
        bars = self.adapter.get_latest_bars("XAU_USD", 4)
        assert len(bars) == 3  # Incomplete candle excluded

    def test_volume_mapped(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=1)
        bars = self.adapter.get_latest_bars("XAU_USD", 1)
        assert bars[0].volume == 100.0

    def test_api_error_propagates(self) -> None:
        self.conn.get.side_effect = OANDAAPIError(500, "Internal error")
        try:
            self.adapter.get_latest_bars("XAU_USD", 5)
            assert False, "Should have raised"  # noqa: B011
        except OANDAAPIError:
            pass

    def test_passes_correct_params(self) -> None:
        self.conn.get.return_value = _make_candles_response(count=1)
        self.adapter.get_latest_bars("XAU_USD", 50)
        self.conn.get.assert_called_once_with(
            "/v3/accounts/101-001-123/instruments/XAU_USD/candles",
            params={"granularity": "M1", "count": "50", "price": "B"},
        )

    def test_custom_granularity_passed_to_api(self) -> None:
        adapter = OANDAMarketDataAdapter(
            connection=self.conn, account_id="101-001-123", granularity="H1"
        )
        self.conn.get.return_value = _make_candles_response(count=1)
        adapter.get_latest_bars("XAU_USD", 50)
        self.conn.get.assert_called_once_with(
            "/v3/accounts/101-001-123/instruments/XAU_USD/candles",
            params={"granularity": "H1", "count": "50", "price": "B"},
        )
