"""Tests for _ensure_data_available in web._run_helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
from aurex_trade.domain.models import BarData
from aurex_trade.web._run_helpers import _ensure_data_available
from aurex_trade.web.tasks import TaskRegistry


def _make_bar(ts: datetime, symbol: str = "XAU_USD") -> BarData:
    return BarData(
        timestamp=ts,
        open=1800.0,
        high=1810.0,
        low=1790.0,
        close=1805.0,
        volume=100.0,
        symbol=symbol,
    )


class TestDataAlreadyAvailable:
    """When local data fully covers the requested range."""

    def test_returns_bars_without_downloading(self, tmp_path: Path) -> None:
        """If bars cover the range, return immediately."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 3, tzinfo=UTC)
        bars = [
            _make_bar(datetime(2025, 1, 1, tzinfo=UTC)),
            _make_bar(datetime(2025, 1, 2, tzinfo=UTC)),
            _make_bar(datetime(2025, 1, 3, tzinfo=UTC)),
        ]

        data_store = HistoricalDataStore(tmp_path)
        data_store.save_bars(bars, "XAU_USD", "M1")

        result = _ensure_data_available(data_store, "XAU_USD", "M1", start, end)

        assert len(result) == 3
        assert result[0].timestamp == start
        assert result[-1].timestamp == end

    def test_no_download_when_range_covered(self, tmp_path: Path) -> None:
        """OANDA downloader is never called when data already exists."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        bars = [
            _make_bar(datetime(2025, 1, 1, tzinfo=UTC)),
            _make_bar(datetime(2025, 1, 2, tzinfo=UTC)),
        ]

        data_store = HistoricalDataStore(tmp_path)
        data_store.save_bars(bars, "XAU_USD", "M1")

        # No patching needed — if download code runs it would fail (no credentials)
        result = _ensure_data_available(data_store, "XAU_USD", "M1", start, end)

        assert len(result) == 2


class TestAutoDownload:
    """When data is missing and credentials are configured."""

    def test_downloads_when_file_missing(self, tmp_path: Path) -> None:
        """Triggers download when CSV file doesn't exist."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        data_store = HistoricalDataStore(tmp_path)

        bars_after_download = [
            _make_bar(datetime(2025, 1, 1, tzinfo=UTC)),
            _make_bar(datetime(2025, 1, 2, tzinfo=UTC)),
        ]

        with (
            patch("aurex_trade.config.OANDAConfig") as mock_config_cls,
            patch("aurex_trade.adapters.oanda.connection.OANDAConnection") as mock_conn_cls,
            patch(
                "aurex_trade.adapters.oanda.downloader.OANDAHistoricalDownloader"
            ) as mock_dl_cls,
        ):
            mock_config = MagicMock()
            mock_config.access_token = "test-token"  # noqa: S105
            mock_config.account_id = "test-account"
            mock_config_cls.return_value = mock_config

            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn

            mock_dl = MagicMock()
            mock_dl.download.side_effect = lambda *a, **kw: (
                data_store.save_bars(bars_after_download, "XAU_USD", "M1"),
                2,
            )[1]
            mock_dl_cls.return_value = mock_dl

            result = _ensure_data_available(
                data_store, "XAU_USD", "M1", start, end
            )

        assert len(result) == 2
        mock_conn.connect.assert_called_once()
        mock_conn.disconnect.assert_called_once()
        mock_dl.download.assert_called_once_with("XAU_USD", "M1", start, end)

    def test_uses_partial_data_without_redownload(self, tmp_path: Path) -> None:
        """Returns existing bars even if they don't cover the full range.

        Avoids redundant downloads when markets are closed (weekends/holidays)
        and OANDA wouldn't return additional data anyway.
        """
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 5, tzinfo=UTC)
        # Existing data only covers Jan 1-2 (e.g. market closed Jan 3-5)
        existing_bars = [
            _make_bar(datetime(2025, 1, 1, tzinfo=UTC)),
            _make_bar(datetime(2025, 1, 2, tzinfo=UTC)),
        ]
        data_store = HistoricalDataStore(tmp_path)
        data_store.save_bars(existing_bars, "XAU_USD", "M1")

        result = _ensure_data_available(data_store, "XAU_USD", "M1", start, end)

        # Returns available bars without attempting download
        assert len(result) == 2

    def test_updates_task_message_during_download(self, tmp_path: Path) -> None:
        """Registry.update_message is called with download progress."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        data_store = HistoricalDataStore(tmp_path)
        task_id = uuid4()
        registry = MagicMock(spec=TaskRegistry)

        bars_after = [_make_bar(datetime(2025, 1, 1, tzinfo=UTC))]

        with (
            patch("aurex_trade.config.OANDAConfig") as mock_config_cls,
            patch("aurex_trade.adapters.oanda.connection.OANDAConnection") as mock_conn_cls,
            patch(
                "aurex_trade.adapters.oanda.downloader.OANDAHistoricalDownloader"
            ) as mock_dl_cls,
        ):
            mock_config = MagicMock()
            mock_config.access_token = "test-token"  # noqa: S105
            mock_config.account_id = "test-account"
            mock_config_cls.return_value = mock_config

            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn

            mock_dl = MagicMock()
            mock_dl.download.side_effect = lambda *a, **kw: (
                data_store.save_bars(bars_after, "XAU_USD", "M1"),
                1,
            )[1]
            mock_dl_cls.return_value = mock_dl

            _ensure_data_available(
                data_store, "XAU_USD", "M1", start, end,
                task_id=task_id, registry=registry,
            )

        registry.update_message.assert_called_once_with(
            task_id, "Downloading XAU_USD (M1) data..."
        )


class TestCredentialsMissing:
    """When OANDA credentials are not configured."""

    def test_raises_value_error_with_guidance(self, tmp_path: Path) -> None:
        """Clear error message when credentials missing."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        data_store = HistoricalDataStore(tmp_path)

        with patch("aurex_trade.config.OANDAConfig") as mock_config_cls:
            mock_config = MagicMock()
            mock_config.access_token = ""
            mock_config.account_id = ""
            mock_config_cls.return_value = mock_config

            with pytest.raises(ValueError, match="OANDA credentials not configured"):
                _ensure_data_available(
                    data_store, "XAU_USD", "M1", start, end
                )

    def test_raises_when_token_missing(self, tmp_path: Path) -> None:
        """Error when only access_token is empty."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        data_store = HistoricalDataStore(tmp_path)

        with patch("aurex_trade.config.OANDAConfig") as mock_config_cls:
            mock_config = MagicMock()
            mock_config.access_token = ""
            mock_config.account_id = "some-account"
            mock_config_cls.return_value = mock_config

            with pytest.raises(ValueError, match="OANDA credentials not configured"):
                _ensure_data_available(
                    data_store, "XAU_USD", "M1", start, end
                )


class TestNoDatesProvided:
    """When start or end is None, cannot auto-download."""

    def test_raises_file_not_found_when_start_none(self, tmp_path: Path) -> None:
        """Cannot download without a start date."""
        data_store = HistoricalDataStore(tmp_path)

        with pytest.raises(FileNotFoundError, match="No data found"):
            _ensure_data_available(
                data_store, "XAU_USD", "M1", None, datetime(2025, 1, 2, tzinfo=UTC)
            )

    def test_raises_file_not_found_when_end_none(self, tmp_path: Path) -> None:
        """Cannot download without an end date."""
        data_store = HistoricalDataStore(tmp_path)

        with pytest.raises(FileNotFoundError, match="No data found"):
            _ensure_data_available(
                data_store, "XAU_USD", "M1", datetime(2025, 1, 1, tzinfo=UTC), None
            )


class TestDownloadError:
    """When the OANDA download fails."""

    def test_propagates_connection_error(self, tmp_path: Path) -> None:
        """Download errors propagate to the caller."""
        start = datetime(2025, 1, 1, tzinfo=UTC)
        end = datetime(2025, 1, 2, tzinfo=UTC)
        data_store = HistoricalDataStore(tmp_path)

        with (
            patch("aurex_trade.config.OANDAConfig") as mock_config_cls,
            patch("aurex_trade.adapters.oanda.connection.OANDAConnection") as mock_conn_cls,
            patch(
                "aurex_trade.adapters.oanda.downloader.OANDAHistoricalDownloader"
            ) as mock_dl_cls,
        ):
            mock_config = MagicMock()
            mock_config.access_token = "test-token"  # noqa: S105
            mock_config.account_id = "test-account"
            mock_config_cls.return_value = mock_config

            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn

            mock_dl = MagicMock()
            mock_dl.download.side_effect = RuntimeError("OANDA API timeout")
            mock_dl_cls.return_value = mock_dl

            with pytest.raises(RuntimeError, match="OANDA API timeout"):
                _ensure_data_available(
                    data_store, "XAU_USD", "M1", start, end
                )

        # Connection should still be disconnected even on error
        mock_conn.disconnect.assert_called_once()
