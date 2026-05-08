"""Session port — defines the contract for session persistence."""

from datetime import datetime
from typing import Protocol

from aurex_trade.domain.models import SessionData, User


class SessionPort(Protocol):
    """Port for persisting and retrieving user sessions."""

    def save_user(self, user: User, last_login: datetime) -> None: ...

    def get_user(self, user_id: str) -> User | None: ...

    def create_session(self, user_id: str, expires_at: datetime) -> str: ...

    def get_session(self, session_id: str) -> SessionData | None: ...

    def extend_session(self, session_id: str, new_expiry: datetime) -> None: ...

    def delete_session(self, session_id: str) -> None: ...

    def cleanup_expired(self) -> int: ...
