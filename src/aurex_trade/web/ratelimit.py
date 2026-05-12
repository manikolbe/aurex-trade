"""Rate limiting configuration and setup using slowapi."""

from __future__ import annotations

import re

from pydantic_settings import BaseSettings, SettingsConfigDict
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}

# Pattern matching slowapi detail: "N per M <unit>" (e.g. "5 per 1 minute")
_RATE_DETAIL_RE = re.compile(r"(\d+)\s+per\s+(\d+)\s+(\w+)")


class RateLimitConfig(BaseSettings):
    """Rate limiting configuration — all values overridable via environment variables."""

    model_config = SettingsConfigDict(env_prefix="RATELIMIT_", env_file=".env", extra="ignore")

    enabled: bool = True
    storage_uri: str = "memory://"
    default: str = "60/minute"
    compute: str = "5/minute"
    bot_control: str = "3/minute"
    read: str = "120/minute"
    auth: str = "10/minute"
    auth_logout: str = "5/minute"


def get_client_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For header or direct connection.

    When behind a reverse proxy (nginx, Caddy), the real client IP is in
    X-Forwarded-For. The proxy must be configured to OVERWRITE this header
    (not append) to prevent client spoofing:

        proxy_set_header X-Forwarded-For $remote_addr;

    Falls back to direct socket address when no header is present.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Take first non-empty IP (leftmost = original client set by proxy)
        for ip in forwarded.split(","):
            cleaned = ip.strip()
            if cleaned:
                return cleaned
    if request.client:
        return request.client.host
    return "127.0.0.1"


def create_limiter(config: RateLimitConfig) -> Limiter:
    """Create a slowapi Limiter instance from configuration."""
    return Limiter(
        key_func=get_client_ip,
        default_limits=[config.default],
        storage_uri=config.storage_uri,
        enabled=config.enabled,
    )


# Module-level instances — imported by route modules for @limiter.limit() decorators.
# The config reads from environment at import time (standard pydantic-settings behavior).
ratelimit_config = RateLimitConfig()
limiter = create_limiter(ratelimit_config)


def reset_limiter() -> None:
    """Reset all rate limit counters. Used in tests for isolation."""
    storage = getattr(limiter, "_storage", None)
    if storage and hasattr(storage, "reset"):
        storage.reset()


def _parse_retry_after(detail: str) -> str:
    """Parse slowapi's detail string to compute a Retry-After value in seconds.

    slowapi detail format: "N per M <unit>" (e.g. "5 per 1 minute").
    We compute: window_seconds / max_requests as the retry interval.
    """
    match = _RATE_DETAIL_RE.search(detail)
    if match:
        max_requests = int(match.group(1))
        duration = int(match.group(2))
        unit = match.group(3).rstrip("s")  # "minutes" → "minute"
        window_seconds = duration * _UNIT_SECONDS.get(unit, 60)
        # Suggest waiting for one slot to free up
        return str(max(1, window_seconds // max(1, max_requests)))
    return "60"


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Handle 429 responses — JSON for API, HTML fragment for HTMX routes.

    IMPORTANT: This must be a sync function. SlowAPIMiddleware uses a sync code
    path internally and falls back to its built-in handler for async handlers.
    """
    retry_seconds = _parse_retry_after(exc.detail) if exc.detail else "60"

    headers = {"Retry-After": retry_seconds}

    if request.url.path.startswith("/htmx"):
        html = (
            '<div class="alert alert-warning">'
            '<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6"'
            ' fill="none" viewBox="0 0 24 24">'
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"'
            ' d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667'
            ' 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34'
            ' 16c-.77 1.333.192 3 1.732 3z" />'
            "</svg>"
            "<span>Too many requests. Please try again shortly.</span>"
            "</div>"
        )
        return HTMLResponse(status_code=429, content=html, headers=headers)

    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": "Please try again shortly",
            "status_code": 429,
        },
        headers=headers,
    )
