"""Authentication routes — login, OAuth callback, logout."""

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from aurex_trade.adapters.google.oauth import GoogleOAuthAdapter
from aurex_trade.adapters.sqlite.session_store import SQLiteSessionStore
from aurex_trade.domain.models import User
from aurex_trade.web.auth.config import AuthConfig

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])


def _make_state(secret: str, next_path: str) -> str:
    """Create a signed state token encoding the timestamp and next path."""
    timestamp = str(int(time.time()))
    payload = f"{timestamp}:{next_path}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{signature}:{payload}"


def _verify_state(secret: str, state: str) -> str | None:
    """Verify state token signature and return the next path, or None if invalid."""
    parts = state.split(":", 2)
    if len(parts) != 3:
        return None
    signature, timestamp_str, next_path = parts

    # Verify signature
    payload = f"{timestamp_str}:{next_path}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(signature, expected):
        return None

    # Check timestamp (10 minute window)
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return None
    if abs(time.time() - timestamp) > 600:
        return None

    return next_path


def create_auth_router(
    auth_config: AuthConfig,
    session_store: SQLiteSessionStore,
    oauth_adapter: GoogleOAuthAdapter,
) -> APIRouter:
    """Create auth router with injected dependencies."""

    @router.get("/login", response_class=HTMLResponse)
    def login_page(
        request: Request, redirect_to: str = Query("/", alias="next")
    ) -> HTMLResponse:
        templates = request.app.state.templates
        response: HTMLResponse = templates.TemplateResponse(
            request, "pages/login.html", {"next_path": redirect_to}
        )
        return response

    @router.get("/google")
    def google_redirect(
        request: Request, redirect_to: str = Query("/", alias="next")
    ) -> RedirectResponse:
        """Redirect to Google OAuth consent screen."""
        # Validate redirect path is relative (prevent open redirect)
        if not redirect_to.startswith("/") or redirect_to.startswith("//"):
            redirect_to = "/"

        state = _make_state(auth_config.secret_key, redirect_to)
        url = oauth_adapter.get_authorization_url(state=state)
        return RedirectResponse(url=url, status_code=302)

    @router.get("/callback", response_model=None)
    def oauth_callback(
        request: Request, code: str = "", state: str = ""
    ) -> HTMLResponse | RedirectResponse:
        """Handle Google OAuth callback — exchange code, validate email, create session."""
        templates = request.app.state.templates

        # Validate state (CSRF protection — secret_key is always set)
        if not state:
            logger.warning("auth.callback_missing_state")
            return RedirectResponse(url="/auth/login", status_code=302)
        verified_path = _verify_state(auth_config.secret_key, state)
        if verified_path is None:
            logger.warning("auth.callback_invalid_state")
            return RedirectResponse(url="/auth/login", status_code=302)
        next_path = verified_path

        if not code:
            logger.warning("auth.callback_missing_code")
            return RedirectResponse(url="/auth/login", status_code=302)

        # Exchange code for user info
        try:
            user_info = oauth_adapter.exchange_code(code)
        except Exception:
            logger.exception("auth.token_exchange_failed")
            return RedirectResponse(url="/auth/login", status_code=302)

        # Check email whitelist (allowed_emails is pre-normalized to lowercase)
        if user_info.email.lower() not in auth_config.allowed_emails:
            logger.warning("auth.denied", email=user_info.email, sub=user_info.sub)
            denied: HTMLResponse = templates.TemplateResponse(
                request, "pages/denied.html", {"attempted_email": user_info.email}, status_code=403
            )
            return denied

        # Upsert user
        now = datetime.now(UTC)
        user = User(
            id=user_info.sub,
            email=user_info.email,
            name=user_info.name,
            avatar_url=user_info.picture,
        )
        session_store.save_user(user, last_login=now)

        # Create session
        expires_at = now + timedelta(hours=auth_config.session_expiry_hours)
        session_id = session_store.create_session(user.id, expires_at)

        logger.info("auth.login", user_email=user.email, provider="google")

        # Set cookie and redirect
        response = RedirectResponse(url=next_path, status_code=302)
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            secure=auth_config.cookie_secure,
            samesite="lax",
            max_age=auth_config.session_expiry_hours * 3600,
        )
        return response

    @router.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        """Destroy session and clear cookie."""
        session_id = request.cookies.get("session_id")
        if session_id:
            session_store.delete_session(session_id)
            user: User | None = getattr(request.state, "user", None)
            if user:
                logger.info("auth.logout", user_email=user.email)

        response = RedirectResponse(url="/auth/login", status_code=302)
        response.delete_cookie("session_id")
        return response

    return router
