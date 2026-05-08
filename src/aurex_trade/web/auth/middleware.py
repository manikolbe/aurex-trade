"""Authentication middleware — validates sessions on every request."""

from datetime import UTC, datetime, timedelta

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from aurex_trade.adapters.sqlite.session_store import SQLiteSessionStore

logger = structlog.get_logger()

# Paths that do not require authentication
PUBLIC_PREFIXES = ("/auth/", "/static/", "/api/health", "/guide/")
PUBLIC_EXACT = ("/favicon.ico",)


def _is_public(path: str) -> bool:
    if path in PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate session cookie and attach user to request state."""

    def __init__(self, app: object, session_store: SQLiteSessionStore, expiry_hours: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._session_store = session_store
        self._expiry_hours = expiry_hours

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Clear structlog context for this request
        structlog.contextvars.clear_contextvars()

        # Skip auth for public paths
        if _is_public(request.url.path):
            return await call_next(request)

        # Read session cookie
        session_id = request.cookies.get("session_id")
        if not session_id:
            return self._unauthenticated_response(request)

        # Validate session
        session = self._session_store.get_session(session_id)
        if session is None:
            logger.info("auth.session_expired", path=request.url.path)
            response = self._unauthenticated_response(request)
            response.delete_cookie("session_id")
            return response

        # Load user
        user = self._session_store.get_user(session.user_id)
        if user is None:
            return self._unauthenticated_response(request)

        # Extend session (sliding expiry)
        new_expiry = datetime.now(UTC) + timedelta(hours=self._expiry_hours)
        self._session_store.extend_session(session.session_id, new_expiry)

        # Attach user to request state
        request.state.user = user

        # Bind user to structlog context for audit trail
        structlog.contextvars.bind_contextvars(user_id=user.id, user_email=user.email)

        return await call_next(request)

    @staticmethod
    def _unauthenticated_response(request: Request) -> Response:
        path = request.url.path

        # HTMX requests: return 401 with redirect header
        if path.startswith("/htmx/") or request.headers.get("HX-Request") == "true":
            return Response(
                status_code=401,
                headers={"HX-Redirect": "/auth/login"},
            )

        # API requests: return 401 JSON
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"error": "Not authenticated", "detail": None, "status_code": 401},
            )

        # Page requests: redirect to login
        return RedirectResponse(url="/auth/login", status_code=302)
