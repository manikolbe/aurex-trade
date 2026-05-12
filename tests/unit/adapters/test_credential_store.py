"""Tests for FernetCredentialStore — encrypted broker credential persistence."""

import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from aurex_trade.adapters.sqlite.credential_store import FernetCredentialStore
from aurex_trade.ports.credential_store import (
    BrokerCredentialInfo,
    BrokerCredentials,
    CredentialDecryptionError,
)


def _generate_key() -> str:
    return Fernet.generate_key().decode()


class TestStoreAndRetrieve:
    def test_roundtrip(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        store.store("user1", "oanda", "001-004-1234567-001", "secret-token-abc", "practice")
        result = store.retrieve("user1", "oanda")

        assert result is not None
        assert isinstance(result, BrokerCredentials)
        assert result.account_id == "001-004-1234567-001"
        assert result.access_token == "secret-token-abc"
        assert result.server == "practice"
        store.close()

    def test_retrieve_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        result = store.retrieve("user1", "oanda")
        assert result is None
        store.close()

    def test_upsert_on_conflict(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        store.store("user1", "oanda", "account-old", "token-old", "practice")
        store.store("user1", "oanda", "account-new", "token-new", "live")

        result = store.retrieve("user1", "oanda")
        assert result is not None
        assert result.account_id == "account-new"
        assert result.access_token == "token-new"
        assert result.server == "live"
        store.close()


class TestDelete:
    def test_delete_removes_credentials(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        store.store("user1", "oanda", "account1", "token1", "practice")
        store.delete("user1", "oanda")

        assert store.retrieve("user1", "oanda") is None
        assert not store.has_credentials("user1", "oanda")
        store.close()

    def test_delete_nonexistent_is_idempotent(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())
        store.delete("user1", "oanda")  # no error
        store.close()


class TestHasCredentials:
    def test_true_when_stored(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())
        store.store("user1", "oanda", "account1", "token1", "practice")

        assert store.has_credentials("user1", "oanda") is True
        store.close()

    def test_false_when_empty(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        assert store.has_credentials("user1", "oanda") is False
        store.close()


class TestUserIsolation:
    def test_user_a_cannot_see_user_b(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        store.store("user-a", "oanda", "account-a", "token-a", "practice")
        store.store("user-b", "oanda", "account-b", "token-b", "live")

        result_a = store.retrieve("user-a", "oanda")
        result_b = store.retrieve("user-b", "oanda")

        assert result_a is not None
        assert result_a.account_id == "account-a"
        assert result_b is not None
        assert result_b.account_id == "account-b"

        # User C has nothing
        assert store.retrieve("user-c", "oanda") is None
        store.close()


class TestGetMaskedInfo:
    def test_returns_masked_account_id(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())
        store.store("user1", "oanda", "001-004-1234567-001", "token", "practice")

        info = store.get_masked_info("user1", "oanda")

        assert info is not None
        assert isinstance(info, BrokerCredentialInfo)
        assert info.broker == "oanda"
        assert info.account_id_masked == "***-001"
        assert info.server == "practice"
        assert info.has_credentials is True
        store.close()

    def test_returns_none_when_not_stored(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())

        info = store.get_masked_info("user1", "oanda")
        assert info is None
        store.close()

    def test_short_account_id_masked(self, tmp_path: Path) -> None:
        store = FernetCredentialStore(tmp_path / "test.db", _generate_key())
        store.store("user1", "oanda", "AB", "token", "practice")

        info = store.get_masked_info("user1", "oanda")
        assert info is not None
        assert info.account_id_masked == "***AB"
        store.close()


class TestEncryptionAtRest:
    def test_raw_db_does_not_contain_plaintext(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        store = FernetCredentialStore(db_path, _generate_key())
        store.store("user1", "oanda", "secret-account-123", "super-secret-token", "practice")
        store.close()

        # Read raw encrypted_data from DB
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute(
            "SELECT encrypted_data FROM broker_credentials WHERE user_id = ? AND broker = ?",
            ("user1", "oanda"),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        raw_blob = row[0]
        # The encrypted blob must not contain plaintext credentials
        assert b"secret-account-123" not in raw_blob
        assert b"super-secret-token" not in raw_blob


class TestInvalidKey:
    def test_empty_key_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            FernetCredentialStore(tmp_path / "test.db", "")

    def test_invalid_key_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid encryption key"):
            FernetCredentialStore(tmp_path / "test.db", "not-a-valid-fernet-key")


class TestWrongKeyDecryption:
    def test_wrong_key_raises_credential_decryption_error(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        key1 = _generate_key()
        key2 = _generate_key()

        # Store with key1
        store1 = FernetCredentialStore(db_path, key1)
        store1.store("user1", "oanda", "account1", "token1", "practice")
        store1.close()

        # Retrieve with key2 — should fail with domain error
        store2 = FernetCredentialStore(db_path, key2)
        with pytest.raises(
            CredentialDecryptionError, match="encryption key may have been rotated"
        ):
            store2.retrieve("user1", "oanda")
        store2.close()

    def test_has_credentials_works_with_wrong_key(self, tmp_path: Path) -> None:
        """has_credentials doesn't decrypt, so works regardless of key."""
        db_path = tmp_path / "test.db"
        key1 = _generate_key()
        key2 = _generate_key()

        store1 = FernetCredentialStore(db_path, key1)
        store1.store("user1", "oanda", "account1", "token1", "practice")
        store1.close()

        store2 = FernetCredentialStore(db_path, key2)
        assert store2.has_credentials("user1", "oanda") is True
        store2.close()

    def test_get_masked_info_works_with_wrong_key(self, tmp_path: Path) -> None:
        """get_masked_info uses plaintext metadata, doesn't decrypt."""
        db_path = tmp_path / "test.db"
        key1 = _generate_key()
        key2 = _generate_key()

        store1 = FernetCredentialStore(db_path, key1)
        store1.store("user1", "oanda", "001-004-1234567-001", "token1", "practice")
        store1.close()

        store2 = FernetCredentialStore(db_path, key2)
        info = store2.get_masked_info("user1", "oanda")
        assert info is not None
        assert info.account_id_masked == "***-001"
        store2.close()
