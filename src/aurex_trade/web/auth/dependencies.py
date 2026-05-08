"""Auth dependencies for FastAPI route injection."""

from fastapi import HTTPException, Request

from aurex_trade.domain.models import User


def get_current_user(request: Request) -> User:
    """Extract current authenticated user from request state.

    Set by AuthMiddleware. Raises 401 if not present (defense-in-depth).
    """
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
