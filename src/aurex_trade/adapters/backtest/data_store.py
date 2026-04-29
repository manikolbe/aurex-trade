"""Historical data store — CSV-based persistence for price bars.

Zero external dependencies beyond Python stdlib.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from aurex_trade.domain.models import BarData


class HistoricalDataStore:
    """Read/write historical bar data as CSV files.

    File naming: {data_dir}/{symbol}_{granularity}.csv
    CSV columns: timestamp,open,high,low,close,volume,symbol
    """

    _FIELDNAMES: ClassVar[list[str]] = [
        "timestamp", "open", "high", "low", "close", "volume", "symbol",
    ]

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def save_bars(self, bars: list[BarData], symbol: str, granularity: str) -> Path:
        """Save bars to a CSV file. Overwrites if file exists.

        Returns:
            Path to the written CSV file.
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        path = self._file_path(symbol, granularity)

        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self._FIELDNAMES)
            writer.writeheader()
            for bar in bars:
                writer.writerow(
                    {
                        "timestamp": bar.timestamp.isoformat(),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "symbol": bar.symbol,
                    }
                )

        return path

    def load_bars(
        self,
        symbol: str,
        granularity: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[BarData]:
        """Load bars from CSV, optionally filtering by date range.

        Args:
            symbol: Trading instrument symbol.
            granularity: Bar granularity (e.g., "M1", "H1").
            start: Include bars at or after this time (UTC).
            end: Include bars at or before this time (UTC).

        Returns:
            List of BarData sorted by timestamp.

        Raises:
            FileNotFoundError: If the CSV file does not exist.
        """
        path = self._file_path(symbol, granularity)
        if not path.exists():
            msg = f"No historical data file found: {path}"
            raise FileNotFoundError(msg)

        bars: list[BarData] = []
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)

                if start and ts < start:
                    continue
                if end and ts > end:
                    continue

                bars.append(
                    BarData(
                        timestamp=ts,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        symbol=row["symbol"],
                    )
                )

        return bars

    def _file_path(self, symbol: str, granularity: str) -> Path:
        return self._data_dir / f"{symbol}_{granularity}.csv"
