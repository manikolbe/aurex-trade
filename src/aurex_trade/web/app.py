"""FastAPI application factory — composition root for the web layer.

The web layer is designed for multi-user isolation. Each authenticated user
has their own credentials, preferences, and data. Shared/operator-level
configuration (from .env) is never used for per-user operations.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aurex_trade.adapters.google.oauth import GoogleOAuthAdapter
from aurex_trade.adapters.sqlite.credential_store import FernetCredentialStore
from aurex_trade.adapters.sqlite.market_data_store import (
    SQLiteMarketDataStore,
    UserDataPreferencesStore,
)
from aurex_trade.adapters.sqlite.session_store import SQLiteSessionStore
from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
from aurex_trade.logging import setup_logging
from aurex_trade.web.auth.config import AuthConfig
from aurex_trade.web.auth.middleware import AuthMiddleware
from aurex_trade.web.auth.router import create_auth_router
from aurex_trade.web.config import WebConfig
from aurex_trade.web.errors import register_error_handlers
from aurex_trade.web.routers import health
from aurex_trade.web.tasks import TaskRegistry

logger = structlog.get_logger()

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"
_PROJECT_ROOT = _WEB_DIR.parent.parent.parent
_DOCS_SITE_DIR = _PROJECT_ROOT / "site"


def _get_strategies_context() -> dict[str, dict[str, str | list[dict[str, str | int | float]]]]:
    """Build a JSON-serializable strategies dict for template context."""
    from aurex_trade.backtest.cli import STRATEGY_METADATA

    result: dict[str, dict[str, str | list[dict[str, str | int | float]]]] = {}
    for name, meta_fn in STRATEGY_METADATA.items():
        meta = meta_fn()
        result[name] = {
            "display_name": meta.display_name,
            "description": meta.description,
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "tooltip": p.tooltip,
                    "default": p.default,
                    "min_value": p.min_value,
                    "max_value": p.max_value,
                }
                for p in meta.params
            ],
        }
    return result


_DB_PATH = Path("data/aurex_trade.db")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manage application startup and shutdown."""
    registry = TaskRegistry(max_workers=2)
    app.state.task_registry = registry

    # Cleanup expired sessions on startup (store is created in create_app)
    session_store: SQLiteSessionStore | None = getattr(app.state, "session_store", None)
    if session_store:
        expired = session_store.cleanup_expired()
        if expired:
            logger.info("web.session_cleanup", deleted=expired)

    logger.info("web.startup", workers=2)
    yield

    # Shutdown: close stores
    market_data_store: SQLiteMarketDataStore | None = getattr(
        app.state, "market_data_store", None
    )
    if market_data_store:
        market_data_store.close()
    preferences_store: UserDataPreferencesStore | None = getattr(
        app.state, "preferences_store", None
    )
    if preferences_store:
        preferences_store.close()
    credential_store: FernetCredentialStore | None = getattr(
        app.state, "credential_store", None
    )
    if credential_store:
        credential_store.close()
    if session_store:
        session_store.close()
    registry.shutdown()
    logger.info("web.shutdown")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AurexTrade",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Error handlers
    register_error_handlers(app)

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # User-facing documentation (built by mkdocs build)
    if _DOCS_SITE_DIR.is_dir():
        app.mount(
            "/guide", StaticFiles(directory=str(_DOCS_SITE_DIR), html=True), name="guide"
        )

    # Templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates

    # Market data and user preferences stores
    market_data_store = SQLiteMarketDataStore(db_path=_DB_PATH)
    app.state.market_data_store = market_data_store
    preferences_store = UserDataPreferencesStore(db_path=_DB_PATH)
    app.state.preferences_store = preferences_store
    user_defaults_store = UserDefaultsStore(db_path=_DB_PATH)
    app.state.user_defaults_store = user_defaults_store

    # Encrypted credential store (per-user broker credentials)
    # Read from environment; dotenv loading ensures .env values are available.
    from dotenv import load_dotenv

    load_dotenv()
    encryption_key = os.environ.get("AUREX_CREDENTIAL_ENCRYPTION_KEY", "")
    if not encryption_key:
        raise SystemExit(
            "AUREX_CREDENTIAL_ENCRYPTION_KEY must be set.\n"
            "Generate: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    credential_store = FernetCredentialStore(db_path=_DB_PATH, encryption_key=encryption_key)
    app.state.credential_store = credential_store

    # Authentication
    auth_config = AuthConfig()
    session_store = SQLiteSessionStore(db_path=_DB_PATH)
    app.state.session_store = session_store
    oauth_adapter = GoogleOAuthAdapter(
        client_id=auth_config.google_client_id,
        client_secret=auth_config.google_client_secret,
        redirect_uri=auth_config.redirect_uri,
    )
    auth_router = create_auth_router(auth_config, session_store, oauth_adapter)
    app.include_router(auth_router)
    app.add_middleware(
        AuthMiddleware,
        session_store=session_store,
        expiry_hours=auth_config.session_expiry_hours,
    )

    # Routers
    app.include_router(health.router)

    # Import and include additional routers (lazy to avoid circular imports)
    from aurex_trade.web.routers import backtest, bot, broker, settings, user_defaults

    app.include_router(backtest.router)
    app.include_router(bot.router)
    app.include_router(broker.router)
    app.include_router(settings.router)
    app.include_router(user_defaults.router)

    # Page routes (serve HTML templates)
    def _user_context(request: Request) -> dict[str, object]:
        """Extract user from request state for template context."""
        return {"user": getattr(request.state, "user", None)}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "pages/index.html", _user_context(request))

    @app.get("/backtest", response_class=HTMLResponse)
    def backtest_page(request: Request) -> HTMLResponse:
        ctx = {**_user_context(request), "strategies": _get_strategies_context()}
        return templates.TemplateResponse(request, "pages/backtest.html", ctx)

    @app.get("/sweep", response_class=HTMLResponse)
    def sweep_page(request: Request) -> HTMLResponse:
        ctx = {**_user_context(request), "strategies": _get_strategies_context()}
        return templates.TemplateResponse(request, "pages/sweep.html", ctx)

    @app.get("/walk-forward", response_class=HTMLResponse)
    def walk_forward_page(request: Request) -> HTMLResponse:
        ctx = {**_user_context(request), "strategies": _get_strategies_context()}
        return templates.TemplateResponse(request, "pages/walk_forward.html", ctx)

    @app.get("/bot", response_class=HTMLResponse)
    def bot_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "pages/bot.html", _user_context(request))

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "pages/settings.html", _user_context(request)
        )

    return app


def main() -> None:
    """Entry point — start uvicorn with the configured app."""
    config = WebConfig()
    setup_logging(log_level=config.log_level)
    logger.info("web.starting", host=config.host, port=config.port)
    uvicorn.run(
        "aurex_trade.web.app:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        reload=config.reload,
    )
