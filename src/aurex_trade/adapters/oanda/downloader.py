"""OANDA historical data downloader — paginated candle fetching.

Downloads historical candles from the OANDA v20 API and persists them
via any HistoricalDataPort implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.domain.models import BarData

if TYPE_CHECKING:
    from aurex_trade.ports.historical_data import HistoricalDataPort

log = structlog.get_logger()

# OANDA maximum candles per request
_MAX_CANDLES_PER_REQUEST = 5000


class OANDAHistoricalDownloader:
    """Downloads historical candles from OANDA and persists via HistoricalDataPort.

    Paginates in chunks of 5000 candles (OANDA API limit).
    Uses the mid-price (average of bid and ask) for OHLC values.
    """

    def __init__(
        self,
        connection: OANDAConnection,
        data_store: HistoricalDataPort,
    ) -> None:
        self._connection = connection
        self._data_store = data_store

    def download(
        self,
        symbol: str,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> int:
        """Download historical candles and persist via HistoricalDataPort.

        Args:
            symbol: Instrument name (e.g., "XAU_USD").
            granularity: Candle granularity (e.g., "M1", "H1", "D").
            start: Start time (UTC).
            end: End time (UTC).

        Returns:
            Total number of candles downloaded.
        """
        all_bars: list[BarData] = []
        current_from = start

        log.info(
            "download_started",
            symbol=symbol,
            granularity=granularity,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        while current_from < end:
            bars = self._fetch_chunk(symbol, granularity, current_from, end)
            if not bars:
                break

            # Filter out bars beyond the end time
            bars = [b for b in bars if b.timestamp <= end]
            all_bars.extend(bars)

            if not bars:
                break

            current_from = bars[-1].timestamp
            log.info(
                "download_chunk",
                fetched=len(bars),
                total=len(all_bars),
                last_timestamp=current_from.isoformat(),
            )

            # If we got fewer than max, we've reached the end of available data
            if len(bars) < _MAX_CANDLES_PER_REQUEST:
                break

        if all_bars:
            self._data_store.save_bars(all_bars, symbol, granularity)
            log.info(
                "download_complete",
                total_bars=len(all_bars),
                symbol=symbol,
                granularity=granularity,
            )

        return len(all_bars)

    def _fetch_chunk(
        self,
        symbol: str,
        granularity: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[BarData]:
        """Fetch a single paginated chunk of candles from OANDA."""
        params: dict[str, str] = {
            "granularity": granularity,
            "from": from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": str(_MAX_CANDLES_PER_REQUEST),
            "price": "M",  # mid-price
        }

        account_id = self._connection._config.account_id
        path = f"/v3/accounts/{account_id}/instruments/{symbol}/candles"
        response = self._connection.get(path, params=params)

        candles = response.get("candles", [])
        bars: list[BarData] = []

        for candle in candles:
            if not candle.get("complete", False):
                continue

            mid = candle["mid"]
            ts_str = candle["time"]
            # OANDA returns RFC3339 timestamps
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

            bars.append(
                BarData(
                    timestamp=ts,
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=float(candle.get("volume", 0)),
                    symbol=symbol,
                )
            )

        return bars
