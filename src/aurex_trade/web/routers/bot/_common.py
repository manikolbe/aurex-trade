"""Shared utilities for bot router endpoints."""

from __future__ import annotations

import structlog

from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
from aurex_trade.web._bot_factory import create_bot_engine
from aurex_trade.web._bot_sessions import (
    ActiveBotSession,
    BotAlreadyRunningError,
    BotSessionManager,
)
from aurex_trade.web.schemas import BotStartRequest
from aurex_trade.web.tasks import TaskRegistry

if __name__ != "__main__":
    from aurex_trade.ports.credential_store import CredentialStorePort

log = structlog.get_logger()


def bot_runner(session_manager: BotSessionManager, user_id: str) -> None:
    """Run the engine and auto-clean the session on exit.

    Submitted to TaskRegistry as the background callable. When the engine
    exits (normally or via crash), the session is removed so the user can
    start a new bot.

    Guards against a race condition: if the user stops this bot and starts a
    new one before this runner's thread exits, the finally block must NOT
    kill the new session. We compare session identity to ensure we only clean
    up our own session.
    """
    session = session_manager.get(user_id)
    if session is None:
        return
    try:
        session.engine.run()
    finally:
        # Defensively clear bound log context on this pooled worker. run() clears it
        # too, but if it raised before reaching its own finally (or before binding),
        # this prevents this run's identity leaking into the next task on this thread.
        structlog.contextvars.clear_contextvars()
        # Only clean up if OUR session is still the active one.
        # If the user already stopped us and started a new bot, the active
        # session will be a different instance — leave it alone.
        current = session_manager.get(user_id)
        if current is session:
            session_manager.stop(user_id)


def start_bot_session(
    user_id: str,
    body: BotStartRequest,
    session_manager: BotSessionManager,
    credential_store: CredentialStorePort,
    registry: TaskRegistry,
    user_defaults_store: UserDefaultsStore | None = None,
) -> ActiveBotSession:
    """Wire engine, register session, submit runner.

    Raises:
        ValueError: If credentials are missing/invalid or strategy unknown.
        BotAlreadyRunningError: If user already has a running bot.
    """
    engine, connection = create_bot_engine(
        user_id=user_id,
        strategy_name=body.strategy_name,
        strategy_params=body.strategy_params,
        risk_params=body.risk_params,
        symbol=body.symbol,
        interval_seconds=body.interval_seconds,
        credential_store=credential_store,
        granularity=body.granularity,
    )

    try:
        session = session_manager.start(
            user_id=user_id,
            engine=engine,
            connection=connection,
            symbol=body.symbol,
            strategy_name=body.strategy_name,
            granularity=body.granularity,
            strategy_params=body.strategy_params,
            risk_params=body.risk_params,
        )
    except BotAlreadyRunningError:
        connection.disconnect()
        raise

    uid = user_id
    registry.submit(
        lambda: bot_runner(session_manager, uid),
        task_type="bot",
    )

    # Auto-save config as user defaults for next time
    if user_defaults_store is not None:
        try:
            user_defaults_store.save_strategy_defaults(
                user_id,
                body.strategy_name,
                body.strategy_params,
                is_preferred=True,
            )
            risk_settings: dict[str, int | float | bool | str] = {
                "max_position": int(body.risk_params.get("max_position_size", 10)),
                "max_daily_loss": float(body.risk_params.get("max_daily_loss", 500.0)),
                "max_trades_per_day": int(body.risk_params.get("max_trades_per_day", 10)),
                "risk_per_trade": float(body.risk_params.get("risk_per_trade", 0.02)),
                "max_drawdown_pct": float(body.risk_params.get("max_drawdown_pct", 0.20)),
                "max_consecutive_losses": int(body.risk_params.get("max_consecutive_losses", 5)),
                "require_stop_loss": bool(body.risk_params.get("require_stop_loss", True)),
                "enabled": bool(body.risk_params.get("enabled", False)),
            }
            user_defaults_store.save_risk_defaults(user_id, risk_settings)
        except Exception:
            log.warning("bot.defaults_save_failed", user_id=user_id)

    log.info(
        "bot.started",
        user_id=user_id,
        strategy=body.strategy_name,
        symbol=body.symbol,
    )
    return session
