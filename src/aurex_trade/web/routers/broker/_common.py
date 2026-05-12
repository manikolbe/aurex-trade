"""Shared constants and validation for the broker feature."""

from fastapi import HTTPException

SUPPORTED_BROKERS = {"oanda"}
ALLOWED_SERVERS = {"practice"}  # "live" disabled until live trading is ready


def validate_broker(broker: str) -> None:
    """Reject unsupported broker names."""
    if broker not in SUPPORTED_BROKERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported broker: {broker!r}. Supported: {', '.join(SUPPORTED_BROKERS)}",
        )
