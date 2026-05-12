"""Fernet-encrypted credential store backed by SQLite.

Part of the web layer's multi-user credential isolation. Each user's broker
credentials are encrypted with a shared Fernet key and stored per-user in SQLite.
CLI tools do NOT use this — they read credentials from environment variables.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from aurex_trade.ports.credential_store import (
    BrokerCredentialInfo,
    BrokerCredentials,
    CredentialDecryptionError,
)


class FernetCredentialStore:
    """Encrypts broker credentials at rest using Fernet (AES-128-CBC + HMAC-SHA256).

    Credentials are stored as a single encrypted JSON blob per (user_id, broker).
    Plaintext metadata (masked account_id, server) is stored alongside for UI
    display without requiring decryption.
    """

    def __init__(self, db_path: Path, encryption_key: str) -> None:
        if not encryption_key:
            msg = (
                "encryption_key must not be empty. "
                "Generate one with: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
            raise ValueError(msg)

        try:
            self._fernet = Fernet(encryption_key.encode())
        except (ValueError, Exception) as exc:
            msg = f"Invalid encryption key: {exc}"
            raise ValueError(msg) from exc

        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
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

    def store(
        self,
        user_id: str,
        broker: str,
        account_id: str,
        access_token: str,
        server: str,
    ) -> None:
        """Encrypt and store credentials. Overwrites any existing entry."""
        payload = json.dumps({
            "account_id": account_id,
            "access_token": access_token,
            "server": server,
        })
        encrypted_data = self._fernet.encrypt(payload.encode())
        account_id_masked = self._mask_account_id(account_id)
        now = datetime.now(tz=UTC).isoformat()

        self._conn.execute(
            """
            INSERT INTO broker_credentials
                (user_id, broker, encrypted_data, account_id_masked, server, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, broker) DO UPDATE SET
                encrypted_data = excluded.encrypted_data,
                account_id_masked = excluded.account_id_masked,
                server = excluded.server,
                updated_at = excluded.updated_at
            """,
            (user_id, broker, encrypted_data, account_id_masked, server, now),
        )
        self._conn.commit()

    def retrieve(self, user_id: str, broker: str) -> BrokerCredentials | None:
        """Decrypt and return credentials, or None if not stored.

        Raises:
            CredentialDecryptionError: If the encryption key is wrong/rotated.
        """
        cursor = self._conn.execute(
            "SELECT encrypted_data FROM broker_credentials WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        try:
            decrypted = self._fernet.decrypt(bytes(row["encrypted_data"]))
        except InvalidToken as exc:
            raise CredentialDecryptionError(
                "Cannot decrypt credentials — encryption key may have been rotated. "
                "Stored credentials are unrecoverable with the current key."
            ) from exc

        data: dict[str, str] = json.loads(decrypted)
        return BrokerCredentials(
            account_id=data["account_id"],
            access_token=data["access_token"],
            server=data["server"],
        )

    def delete(self, user_id: str, broker: str) -> None:
        """Remove stored credentials for a user/broker pair."""
        self._conn.execute(
            "DELETE FROM broker_credentials WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        )
        self._conn.commit()

    def has_credentials(self, user_id: str, broker: str) -> bool:
        """Check if credentials exist without decrypting."""
        cursor = self._conn.execute(
            "SELECT 1 FROM broker_credentials WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        )
        return cursor.fetchone() is not None

    def get_masked_info(self, user_id: str, broker: str) -> BrokerCredentialInfo | None:
        """Return non-sensitive metadata from plaintext columns (no decryption)."""
        cursor = self._conn.execute(
            "SELECT broker, account_id_masked, server FROM broker_credentials"
            " WHERE user_id = ? AND broker = ?",
            (user_id, broker),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return BrokerCredentialInfo(
            broker=row["broker"],
            account_id_masked=row["account_id_masked"],
            server=row["server"],
            has_credentials=True,
        )

    @staticmethod
    def _mask_account_id(account_id: str) -> str:
        """Mask all but the last 4 characters of an account ID."""
        if len(account_id) <= 4:
            return "***" + account_id
        return "***" + account_id[-4:]
