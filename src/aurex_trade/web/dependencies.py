"""FastAPI dependency injection callables."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from aurex_trade.adapters.sqlite.market_data_store import (
        SQLiteMarketDataStore,
        UserDataPreferencesStore,
    )
    from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
    from aurex_trade.web.tasks import TaskRegistry


def get_task_registry(request: Request) -> TaskRegistry:
    """Retrieve the TaskRegistry singleton from app state."""
    registry: TaskRegistry = request.app.state.task_registry
    return registry


def get_market_data_store(request: Request) -> SQLiteMarketDataStore:
    """Retrieve the SQLiteMarketDataStore singleton from app state."""
    store: SQLiteMarketDataStore = request.app.state.market_data_store
    return store


def get_preferences_store(request: Request) -> UserDataPreferencesStore:
    """Retrieve the UserDataPreferencesStore singleton from app state."""
    store: UserDataPreferencesStore = request.app.state.preferences_store
    return store


def get_user_defaults_store(request: Request) -> UserDefaultsStore:
    """Retrieve the UserDefaultsStore singleton from app state."""
    store: UserDefaultsStore = request.app.state.user_defaults_store
    return store
