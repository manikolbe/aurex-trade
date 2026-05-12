"""Credential store port — abstraction for encrypted broker credential persistence."""

from dataclasses import dataclass
from typing import Protocol


class CredentialDecryptionError(Exception):
    """Raised when stored credentials cannot be decrypted.

    Typically caused by a rotated or incorrect encryption key.
    This is a domain exception — adapter implementations must catch
    their library-specific decryption errors and re-raise as this.
    """


@dataclass(frozen=True)
class BrokerCredentials:
    """Full decrypted credential set — never returned to frontend."""

    account_id: str
    access_token: str
    server: str


@dataclass(frozen=True)
class BrokerCredentialInfo:
    """Masked credential metadata — safe for API responses.

    Derived from plaintext metadata columns, never requires decryption.
    """

    broker: str
    account_id_masked: str  # e.g. "***1234"
    server: str
    has_credentials: bool


class CredentialStorePort(Protocol):
    """Port for storing and retrieving encrypted broker credentials.

    Implementations encrypt credentials at rest. The web layer uses this
    for per-user credential isolation. CLI tools do NOT use this port —
    they read from environment variables directly (single-operator context).
    """

    def store(
        self,
        user_id: str,
        broker: str,
        account_id: str,
        access_token: str,
        server: str,
    ) -> None: ...

    def retrieve(self, user_id: str, broker: str) -> BrokerCredentials | None:
        """Return decrypted credentials or None if not stored.

        Raises:
            CredentialDecryptionError: If the encryption key is wrong/rotated.
        """
        ...

    def delete(self, user_id: str, broker: str) -> None: ...

    def has_credentials(self, user_id: str, broker: str) -> bool: ...

    def get_masked_info(self, user_id: str, broker: str) -> BrokerCredentialInfo | None:
        """Return non-sensitive metadata without decrypting the credential blob."""
        ...
