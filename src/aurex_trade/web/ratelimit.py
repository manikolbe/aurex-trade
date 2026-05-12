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
    # Trusted proxy IPs that may set X-Forwarded-For.
    # Comma-separated list. If empty, X-Forwarded-For is never trusted.
    trusted_proxies: str = ""


def get_client_ip(request: Request) -> str:
    """Extract client IP, only trusting X-Forwarded-For from known proxies.

    Security: An attacker can spoof X-Forwarded-For to bypass rate limits.
    We only parse it when the direct connection comes from a trusted proxy.
    In development (TestClient), request.client.host is "testclient" which
    won't match any trusted proxy — tests must use X-Forwarded-For headers
    AND configure trusted_proxies, or rely on the direct client IP.
    """
    direct_ip = request.client.host if request.client else "127.0.0.1"

    # Only parse X-Forwarded-For if the direct connection is from a trusted proxy
    trusted = ratelimit_config.trusted_proxies
    if trusted:
        trusted_set = {ip.strip() for ip in trusted.split(",") if ip.strip()}
        if direct_ip in trusted_set:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # Take first non-empty IP (leftmost = original client)
                for ip in forwarded.split(","):
                    cleaned = ip.strip()
                    if cleaned:
                        return cleaned

    return direct_ip


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


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Handle 429 responses — JSON for API, HTML fragment for HTMX routes."""
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
