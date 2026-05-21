"""SQLite session store — persistent storage for auth sessions.

Implements SessionPort using Python's built-in sqlite3 module.
Uses the same schema.sql as SQLiteRepository (shared database file).
"""

import secrets
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from aurex_trade.domain.models import SessionData, User


class SQLiteSessionStore:
    """SessionPort implementation backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False is safe here: WAL mode handles concurrent reads,
        # and session operations are simple atomic statements (no multi-statement txns).
        # Required because ASGI middleware may run on a different thread than startup.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()

    def _apply_schema(self) -> None:
        schema_sql = (
            resources.files("aurex_trade.adapters.sqlite")
            .joinpath("schema.sql")
            .read_text(encoding="utf-8")
        )
        self._conn.executescript(schema_sql)

    def close(self) -> None:
        self._conn.close()

    def save_user(self, user: User, last_login: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO users (id, email, name, avatar_url, created_at, last_login)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                email = excluded.email,
                name = excluded.name,
                avatar_url = excluded.avatar_url,
                last_login = excluded.last_login
            """,
            (
                user.id,
                user.email,
                user.name,
                user.avatar_url,
                last_login.isoformat(),
                last_login.isoformat(),
            ),
        )
        self._conn.commit()

    def get_user(self, user_id: str) -> User | None:
        cursor = self._conn.execute(
            "SELECT id, email, name, avatar_url FROM users WHERE id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            avatar_url=row["avatar_url"],
        )

    def create_session(self, user_id: str, expires_at: datetime) -> str:
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        self._conn.execute(
            """
            INSERT INTO sessions (id, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, user_id, now.isoformat(), expires_at.isoformat()),
        )
        self._conn.commit()
        return session_id

    def get_session(self, session_id: str) -> SessionData | None:
        now = datetime.now(UTC)
        cursor = self._conn.execute(
            "SELECT id, user_id, created_at, expires_at FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at <= now:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
            return None
        return SessionData(
            session_id=row["id"],
            user_id=row["user_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=expires_at,
        )

    def extend_session(self, session_id: str, new_expiry: datetime) -> None:
        self._conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE id = ?",
            (new_expiry.isoformat(), session_id),
        )
        self._conn.commit()

    def delete_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._conn.commit()

    def cleanup_expired(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = self._conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        self._conn.commit()
        return cursor.rowcount
