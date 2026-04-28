"""OANDA market data adapter — implements MarketDataPort via v20 candles endpoint."""

from datetime import UTC, datetime

import structlog

from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.domain.models import BarData

log = structlog.get_logger()


class OANDAMarketDataAdapter:
    """Fetch historical price bars from the OANDA v20 REST API.

    Uses bid prices for OHLC and tick volume for the volume field.
    Only returns complete (closed) candles — the in-progress bar is excluded.
    """

    def __init__(self, connection: OANDAConnection, account_id: str) -> None:
        self._connection = connection
        self._account_id = account_id

    def get_latest_bars(self, symbol: str, count: int) -> list[BarData]:
        """Fetch the most recent completed 1-minute bars for a symbol."""
        data = self._connection.get(
            f"/v3/accounts/{self._account_id}/instruments/{symbol}/candles",
            params={
                "granularity": "M1",
                "count": str(count),
                "price": "B",  # Bid prices
            },
        )

        bars: list[BarData] = []
        for candle in data.get("candles", []):
            if not candle.get("complete", False):
                continue

            bid = candle["bid"]
            bars.append(
                BarData(
                    timestamp=_parse_timestamp(candle["time"]),
                    open=float(bid["o"]),
                    high=float(bid["h"]),
                    low=float(bid["l"]),
                    close=float(bid["c"]),
                    volume=float(candle.get("volume", 0)),
                    symbol=symbol,
                )
            )

        log.debug("oanda_bars_fetched", symbol=symbol, count=len(bars))
        return bars


def _parse_timestamp(time_str: str) -> datetime:
    """Parse an OANDA RFC3339 timestamp to a UTC-aware datetime."""
    # OANDA returns timestamps like "2024-01-15T14:30:00.000000000Z"
    # Strip sub-microsecond precision that Python can't parse
    clean = time_str.replace("Z", "+00:00")
    if "." in clean:
        dot_idx = clean.index(".")
        plus_idx = clean.index("+", dot_idx)
        frac = clean[dot_idx + 1 : plus_idx]
        # Truncate to 6 digits (microseconds)
        frac = frac[:6]
        clean = clean[: dot_idx + 1] + frac + clean[plus_idx:]

    return datetime.fromisoformat(clean).astimezone(UTC)
