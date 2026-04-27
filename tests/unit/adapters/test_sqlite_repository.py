"""Tests for the SQLite repository adapter."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from aurex_trade.adapters.sqlite.repository import SQLiteRepository
from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


def _make_repo(tmp_path: Path) -> SQLiteRepository:
    return SQLiteRepository(db_path=tmp_path / "test.db")


class TestSchemaCreation:
    def test_creates_db_and_tables(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        # Verify tables exist by querying sqlite_master
        cursor = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in cursor.fetchall()]
        assert "signals" in tables
        assert "decisions" in tables
        assert "trades" in tables
        assert "positions" in tables
        repo.close()

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        cursor = repo._conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode == "wal"
        repo.close()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        repo = SQLiteRepository(db_path=tmp_path / "sub" / "dir" / "test.db")
        assert (tmp_path / "sub" / "dir" / "test.db").exists()
        repo.close()

    def test_schema_is_idempotent(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        repo.close()
        # Re-open same DB — schema applied again without error
        repo2 = SQLiteRepository(db_path=tmp_path / "test.db")
        repo2.close()


class TestSaveAndRetrieve:
    def test_save_signal(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        signal = Signal(symbol="GLD", signal_type=SignalType.LONG, strategy_name="test")
        repo.save_signal(signal)

        cursor = repo._conn.execute("SELECT * FROM signals WHERE id = ?", (str(signal.id),))
        row = cursor.fetchone()
        assert row is not None
        assert row["symbol"] == "GLD"
        assert row["signal_type"] == "long"
        assert row["strategy_name"] == "test"
        repo.close()

    def test_save_signal_with_metadata(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        signal = Signal(
            symbol="GLD",
            signal_type=SignalType.LONG,
            strategy_name="test",
            metadata={"fast_sma": "185.0", "slow_sma": "180.0"},
        )
        repo.save_signal(signal)

        cursor = repo._conn.execute("SELECT metadata FROM signals WHERE id = ?", (str(signal.id),))
        row = cursor.fetchone()
        assert '"fast_sma": "185.0"' in row["metadata"]
        repo.close()

    def test_save_decision(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        decision = RiskDecision(action=RiskAction.APPROVED, reason="ok")
        repo.save_decision(decision)

        cursor = repo._conn.execute(
            "SELECT * FROM decisions WHERE signal_id = ?", (str(decision.signal_id),)
        )
        row = cursor.fetchone()
        assert row is not None
        assert row["action"] == "approved"
        assert row["reason"] == "ok"
        repo.close()

    def test_save_trade(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        trade = Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0)
        repo.save_trade(trade)

        cursor = repo._conn.execute("SELECT * FROM trades WHERE id = ?", (str(trade.id),))
        row = cursor.fetchone()
        assert row is not None
        assert row["symbol"] == "GLD"
        assert row["side"] == "buy"
        assert row["quantity"] == 1.0
        assert row["price"] == 180.0
        repo.close()

    def test_save_position_upserts_by_symbol(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pos1 = Position(symbol="GLD", quantity=5.0)
        pos2 = Position(symbol="GLD", quantity=10.0)
        repo.save_position(pos1)
        repo.save_position(pos2)
        result = repo.get_current_position("GLD")
        assert result is not None
        assert result.quantity == 10.0
        repo.close()


class TestGetCurrentPosition:
    def test_returns_none_when_no_position(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        assert repo.get_current_position("GLD") is None
        repo.close()

    def test_returns_position_for_correct_symbol(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        repo.save_position(Position(symbol="GLD", quantity=5.0))
        repo.save_position(Position(symbol="SPY", quantity=3.0))
        result = repo.get_current_position("GLD")
        assert result is not None
        assert result.symbol == "GLD"
        assert result.quantity == 5.0
        repo.close()


class TestGetTradesToday:
    def test_returns_empty_when_no_trades(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        assert repo.get_trades_today("GLD") == []
        repo.close()

    def test_returns_only_today_trades(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        today_trade = Trade(
            symbol="GLD",
            side=OrderSide.BUY,
            quantity=1.0,
            price=180.0,
            timestamp=datetime.now(UTC),
        )
        yesterday_trade = Trade(
            symbol="GLD",
            side=OrderSide.SELL,
            quantity=1.0,
            price=181.0,
            timestamp=datetime.now(UTC) - timedelta(days=1),
        )
        repo.save_trade(today_trade)
        repo.save_trade(yesterday_trade)
        result = repo.get_trades_today("GLD")
        assert len(result) == 1
        assert result[0].id == today_trade.id
        repo.close()

    def test_filters_by_symbol(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        repo.save_trade(
            Trade(symbol="GLD", side=OrderSide.BUY, quantity=1.0, price=180.0)
        )
        repo.save_trade(
            Trade(symbol="SPY", side=OrderSide.BUY, quantity=1.0, price=400.0)
        )
        assert len(repo.get_trades_today("GLD")) == 1
        assert len(repo.get_trades_today("SPY")) == 1
        repo.close()


class TestDataPersistence:
    def test_data_survives_close_and_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "persist.db"
        repo = SQLiteRepository(db_path=db_path)
        repo.save_trade(Trade(symbol="GLD", side=OrderSide.BUY, quantity=5.0, price=180.0))
        repo.save_position(Position(symbol="GLD", quantity=5.0, average_cost=180.0))
        repo.close()

        # Reopen and verify data is still there
        repo2 = SQLiteRepository(db_path=db_path)
        trades = repo2.get_trades_today("GLD")
        assert len(trades) == 1
        assert trades[0].quantity == 5.0

        pos = repo2.get_current_position("GLD")
        assert pos is not None
        assert pos.quantity == 5.0
        repo2.close()
