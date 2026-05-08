"""Tests for the SQLite session store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from aurex_trade.adapters.sqlite.session_store import SQLiteSessionStore
from aurex_trade.domain.models import User


def _make_store(tmp_path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(db_path=tmp_path / "test.db")


def _make_user() -> User:
    return User(id="google-sub-123", email="test@gmail.com", name="Test User", avatar_url="")


class TestSchemaCreation:
    def test_creates_users_and_sessions_tables(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in cursor.fetchall()]
        assert "users" in tables
        assert "sessions" in tables
        store.close()

    def test_schema_is_idempotent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.close()
        store2 = SQLiteSessionStore(db_path=tmp_path / "test.db")
        store2.close()


class TestUserOperations:
    def test_save_and_get_user(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        retrieved = store.get_user(user.id)
        assert retrieved is not None
        assert retrieved.id == user.id
        assert retrieved.email == user.email
        assert retrieved.name == user.name
        store.close()

    def test_save_user_upserts_on_conflict(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        updated_user = User(
            id=user.id, email="new@gmail.com", name="Updated Name", avatar_url="https://pic.url"
        )
        store.save_user(updated_user, last_login=now + timedelta(hours=1))

        retrieved = store.get_user(user.id)
        assert retrieved is not None
        assert retrieved.email == "new@gmail.com"
        assert retrieved.name == "Updated Name"
        assert retrieved.avatar_url == "https://pic.url"
        store.close()

    def test_get_user_returns_none_for_missing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_user("nonexistent") is None
        store.close()


class TestSessionOperations:
    def test_create_and_get_session(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        expires_at = now + timedelta(hours=48)
        session_id = store.create_session(user.id, expires_at)

        session = store.get_session(session_id)
        assert session is not None
        assert session.session_id == session_id
        assert session.user_id == user.id
        store.close()

    def test_get_session_returns_none_for_missing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_session("nonexistent") is None
        store.close()

    def test_get_session_returns_none_and_deletes_expired(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        # Create already-expired session
        expired = now - timedelta(hours=1)
        session_id = store.create_session(user.id, expired)

        assert store.get_session(session_id) is None

        # Verify it was deleted from the database
        cursor = store._conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,))
        assert cursor.fetchone()[0] == 0
        store.close()

    def test_extend_session(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        expires_at = now + timedelta(hours=48)
        session_id = store.create_session(user.id, expires_at)

        new_expiry = now + timedelta(hours=96)
        store.extend_session(session_id, new_expiry)

        session = store.get_session(session_id)
        assert session is not None
        # Compare with second precision (ISO format round-trip)
        assert session.expires_at.replace(microsecond=0) == new_expiry.replace(microsecond=0)
        store.close()

    def test_delete_session(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        session_id = store.create_session(user.id, now + timedelta(hours=48))
        store.delete_session(session_id)

        assert store.get_session(session_id) is None
        store.close()

    def test_cleanup_expired(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        # Create 2 expired sessions and 1 valid
        store.create_session(user.id, now - timedelta(hours=2))
        store.create_session(user.id, now - timedelta(hours=1))
        valid_id = store.create_session(user.id, now + timedelta(hours=48))

        deleted = store.cleanup_expired()
        assert deleted == 2

        # Valid session still exists
        assert store.get_session(valid_id) is not None
        store.close()

    def test_session_id_is_cryptographically_random(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        user = _make_user()
        now = datetime.now(UTC)
        store.save_user(user, last_login=now)

        ids = {store.create_session(user.id, now + timedelta(hours=48)) for _ in range(10)}
        assert len(ids) == 10  # All unique
        for sid in ids:
            assert len(sid) >= 32  # token_urlsafe(32) produces 43 chars
        store.close()
