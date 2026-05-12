"""Rate limiting configuration and setup using slowapi."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response


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

    When behind a reverse proxy (nginx, Caddy, etc.), the real client IP
    is in X-Forwarded-For. Falls back to direct socket address.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
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


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Handle 429 responses — JSON for API, HTML fragment for HTMX routes."""
    retry_after = exc.detail.split(" ")[-1] if exc.detail else "60"
    # slowapi detail format: "Rate limit exceeded: N per M period"
    # Extract a reasonable retry value
    try:
        retry_seconds = str(int(retry_after))
    except (ValueError, TypeError):
        retry_seconds = "60"

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
            f"<span>Rate limit exceeded. Try again in {retry_seconds} seconds.</span>"
            "</div>"
        )
        return HTMLResponse(status_code=429, content=html, headers=headers)

    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "detail": f"Try again in {retry_seconds} seconds",
            "status_code": 429,
        },
        headers=headers,
    )
