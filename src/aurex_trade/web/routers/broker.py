"""Broker credential management API endpoints.

Part of the multi-user web layer. Each user stores their own encrypted broker
credentials. Tokens are never returned to the frontend or logged after save.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from aurex_trade.adapters.oanda.connection import OANDAConnection, OANDAConnectionError
from aurex_trade.adapters.sqlite.credential_store import FernetCredentialStore
from aurex_trade.config import OANDAConfig
from aurex_trade.domain.models import User
from aurex_trade.ports.credential_store import CredentialDecryptionError
from aurex_trade.web.auth.dependencies import get_current_user
from aurex_trade.web.dependencies import get_credential_store
from aurex_trade.web.schemas import (
    BrokerCredentialRequest,
    BrokerStatusResponse,
    BrokerTestRequest,
    BrokerTestResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/api/broker", tags=["broker"])

_SUPPORTED_BROKERS = {"oanda"}
_ALLOWED_SERVERS = {"practice"}  # "live" disabled until live trading is ready


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _validate_broker(broker: str) -> None:
    """Reject unsupported broker names."""
    if broker not in _SUPPORTED_BROKERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported broker: {broker!r}. Supported: {', '.join(_SUPPORTED_BROKERS)}",
        )


def _render_broker_form(
    request: Request,
    has_credentials: bool,
    account_id_masked: str,
    server: str,
) -> HTMLResponse:
    """Render the broker form partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request,
        "partials/broker_form.html",
        {
            "has_credentials": has_credentials,
            "account_id_masked": account_id_masked,
            "server": server,
        },
    )


@router.get("/status", response_model=None)
def get_broker_status(
    request: Request,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse | BrokerStatusResponse:
    """Return masked credential info for the current user's broker."""
    info = store.get_masked_info(user.id, "oanda")

    has_credentials = info is not None
    account_id_masked = info.account_id_masked if info else ""
    server = info.server if info else "practice"

    if _is_htmx(request):
        return _render_broker_form(request, has_credentials, account_id_masked, server)

    return BrokerStatusResponse(
        broker="oanda",
        has_credentials=has_credentials,
        account_id_masked=account_id_masked,
        server=server,
    )


@router.put("/credentials", response_model=None)
def save_credentials(
    request: Request,
    req: BrokerCredentialRequest,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse | BrokerStatusResponse:
    """Save broker credentials (full replacement). Token never returned."""
    _validate_broker(req.broker)
    if req.server not in _ALLOWED_SERVERS:
        raise HTTPException(status_code=422, detail="Live trading is not yet available.")

    store.store(
        user_id=user.id,
        broker=req.broker,
        account_id=req.account_id,
        access_token=req.access_token,
        server=req.server,
    )
    logger.info("broker.credentials_updated", broker=req.broker, user_id=user.id)

    info = store.get_masked_info(user.id, req.broker)
    assert info is not None  # just stored, must exist

    if _is_htmx(request):
        return _render_broker_form(
            request, info.has_credentials, info.account_id_masked, info.server
        )

    return BrokerStatusResponse(
        broker=info.broker,
        has_credentials=info.has_credentials,
        account_id_masked=info.account_id_masked,
        server=info.server,
    )


@router.delete("/credentials", response_model=None)
def delete_credentials(
    request: Request,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse | BrokerStatusResponse:
    """Remove stored broker credentials."""
    store.delete(user.id, "oanda")
    logger.info("broker.credentials_deleted", broker="oanda", user_id=user.id)

    if _is_htmx(request):
        return _render_broker_form(request, False, "", "practice")

    return BrokerStatusResponse(
        broker="oanda",
        has_credentials=False,
        account_id_masked="",
        server="practice",
    )


@router.post("/test", response_model=None)
def test_connection(
    request: Request,
    req: BrokerTestRequest,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse | BrokerTestResponse:
    """Test broker connection with stored or provided credentials.

    Two explicit modes:
    - use_stored=true: test credentials already saved in the store
    - use_stored=false: test credentials provided in this request (before saving)
    """
    _validate_broker(req.broker)

    if req.use_stored:
        try:
            creds = store.retrieve(user.id, req.broker)
        except CredentialDecryptionError:
            return _test_result(
                request, False,
                "Cannot decrypt stored credentials. The encryption key may have changed.",
            )
        if creds is None:
            return _test_result(
                request, False, "No stored credentials found. Save credentials first."
            )
        account_id = creds.account_id
        access_token = creds.access_token
        server = creds.server
    else:
        if not req.account_id or not req.access_token:
            return _test_result(
                request, False,
                "Account ID and API token are required.",
            )
        account_id = req.account_id
        access_token = req.access_token
        server = req.server

    # Test the connection
    config = OANDAConfig(access_token=access_token, account_id=account_id, server=server)
    conn = OANDAConnection(config)
    try:
        conn.connect()
        conn.disconnect()
    except OANDAConnectionError as exc:
        return _test_result(request, False, str(exc))

    return _test_result(request, True, "Connected successfully.")


def _test_result(
    request: Request, success: bool, message: str
) -> HTMLResponse | BrokerTestResponse:
    """Return test result as HTML partial (HTMX) or JSON (API)."""
    if _is_htmx(request):
        templates = _get_templates(request)
        return templates.TemplateResponse(
            request,
            "partials/broker_status.html",
            {"success": success, "message": message},
        )
    return BrokerTestResponse(success=success, message=message)
