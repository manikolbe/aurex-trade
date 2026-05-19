"""Per-user bot session manager — thread-safe, in-memory registry."""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import structlog

from aurex_trade.engine.trading_engine import TradingEngine

log = structlog.get_logger()


class Disconnectable(Protocol):
    """Anything that exposes a disconnect() method (broker connections, etc.)."""

    def disconnect(self) -> None: ...


class BotAlreadyRunningError(Exception):
    """Raised when attempting to start a bot for a user who already has one running."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"Bot already running for user {user_id}")
        self.user_id = user_id


@dataclass
class ActiveBotSession:
    """One user's running bot session. Mutable runtime tracking record."""

    engine: TradingEngine
    connection: Disconnectable
    user_id: str
    started_at: datetime
    symbol: str
    strategy_name: str


class BotSessionManager:
    """Thread-safe, in-memory registry of active bot sessions keyed by user_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, ActiveBotSession] = {}
        self._lock: threading.Lock = threading.Lock()

    def start(
        self,
        user_id: str,
        engine: TradingEngine,
        connection: Disconnectable,
        symbol: str,
        strategy_name: str,
    ) -> ActiveBotSession:
        """Register a new bot session. Raises BotAlreadyRunningError if one exists."""
        with self._lock:
            if user_id in self._sessions:
                raise BotAlreadyRunningError(user_id)
            session = ActiveBotSession(
                engine=engine,
                connection=connection,
                user_id=user_id,
                started_at=datetime.now(UTC),
                symbol=symbol,
                strategy_name=strategy_name,
            )
            self._sessions[user_id] = session

        log.info(
            "bot_session.started",
            user_id=user_id,
            symbol=symbol,
            strategy=strategy_name,
        )
        return session

    def stop(self, user_id: str) -> None:
        """Stop and remove a user's bot session. Idempotent — no-op if not running."""
        with self._lock:
            session = self._sessions.pop(user_id, None)

        if session is None:
            return

        try:
            session.engine.stop()
        except Exception:
            log.exception("bot_session.engine_stop_failed", user_id=user_id)

        try:
            session.connection.disconnect()
        except Exception:
            log.exception(
                "bot_session.connection_disconnect_failed", user_id=user_id
            )

        log.info("bot_session.stopped", user_id=user_id)

    def get(self, user_id: str) -> ActiveBotSession | None:
        with self._lock:
            return self._sessions.get(user_id)

    def is_running(self, user_id: str) -> bool:
        return self.get(user_id) is not None
