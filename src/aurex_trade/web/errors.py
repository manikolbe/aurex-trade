"""Structured JSON error handlers for the web API."""

from __future__ import annotations

import traceback

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger()


def _is_htmx_request(request: Request) -> bool:
    """Check if the request originated from HTMX or targets an HTML route."""
    return request.url.path.startswith("/htmx")


def _error_json(status_code: int, error: str, detail: str | None = None) -> JSONResponse:
    """Build a consistent JSON error response."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "status_code": status_code},
    )


def _error_html(status_code: int, message: str) -> HTMLResponse:
    """Build an HTML error fragment for HTMX responses."""
    html = (
        '<div class="alert alert-error">'
        '<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6"'
        ' fill="none" viewBox="0 0 24 24">'
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"'
        ' d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />'
        "</svg>"
        f"<span>{message}</span>"
        "</div>"
    )
    return HTMLResponse(status_code=status_code, content=html)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse | HTMLResponse:
    """Handle HTTPException — return JSON or HTML depending on route."""
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)

    if _is_htmx_request(request):
        return _error_html(exc.status_code, detail)

    return _error_json(status_code=exc.status_code, error=detail)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse | HTMLResponse:
    """Handle RequestValidationError — return user-friendly field errors."""
    field_errors: list[str] = []
    for err in exc.errors():
        loc = " → ".join(str(part) for part in err["loc"] if part != "body")
        msg = err["msg"]
        field_errors.append(f"{loc}: {msg}" if loc else msg)

    message = "; ".join(field_errors)

    if _is_htmx_request(request):
        return _error_html(422, message)

    return _error_json(status_code=422, error="Validation error", detail=message)


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse | HTMLResponse:
    """Catch-all handler — log traceback, return safe 500 response."""
    logger.error(
        "web.unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_type=type(exc).__name__,
        traceback="".join(traceback.format_exception(exc)),
    )

    if _is_htmx_request(request):
        return _error_html(500, "Internal server error")

    return _error_json(status_code=500, error="Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
