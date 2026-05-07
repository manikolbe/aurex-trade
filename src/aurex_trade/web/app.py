"""FastAPI application factory — composition root for the web layer."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aurex_trade.logging import setup_logging
from aurex_trade.web.config import WebConfig
from aurex_trade.web.errors import register_error_handlers
from aurex_trade.web.routers import health
from aurex_trade.web.tasks import TaskRegistry

logger = structlog.get_logger()

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Manage application startup and shutdown."""
    registry = TaskRegistry(max_workers=2)
    app.state.task_registry = registry
    logger.info("web.startup", workers=2)
    yield
    registry.shutdown()
    logger.info("web.shutdown")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="aurexTrade",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Error handlers
    register_error_handlers(app)

    # Static files
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Templates
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.templates = templates

    # Routers
    app.include_router(health.router)

    # Import and include additional routers (lazy to avoid circular imports)
    from aurex_trade.web.routers import backtest, bot, htmx, settings

    app.include_router(backtest.router)
    app.include_router(bot.router)
    app.include_router(settings.router)
    app.include_router(htmx.router)

    # Page routes (serve HTML templates)
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "pages/index.html")

    @app.get("/backtest", response_class=HTMLResponse)
    def backtest_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "pages/backtest.html", {"strategies": _get_strategies_context()}
        )

    @app.get("/sweep", response_class=HTMLResponse)
    def sweep_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "pages/sweep.html", {"strategies": _get_strategies_context()}
        )

    @app.get("/walk-forward", response_class=HTMLResponse)
    def walk_forward_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "pages/walk_forward.html", {"strategies": _get_strategies_context()}
        )

    @app.get("/bot", response_class=HTMLResponse)
    def bot_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "pages/bot.html")

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "pages/settings.html")

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
