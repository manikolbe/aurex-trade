"""Health check endpoint."""

from datetime import UTC, datetime

from fastapi import APIRouter

from aurex_trade.web.ratelimit import limiter
from aurex_trade.web.schemas import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
@limiter.exempt  # type: ignore[untyped-decorator]
def health() -> HealthResponse:
    """Return service health status."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(UTC),
        version="0.1.0",
    )
