"""Broker credential management — HTMX endpoints returning HTML fragments."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from aurex_trade.adapters.oanda.connection import OANDAConnection, OANDAConnectionError
from aurex_trade.adapters.sqlite.credential_store import FernetCredentialStore
from aurex_trade.config import OANDAConfig
from aurex_trade.domain.models import User
from aurex_trade.ports.credential_store import CredentialDecryptionError
from aurex_trade.web.auth.dependencies import get_current_user
from aurex_trade.web.dependencies import get_credential_store
from aurex_trade.web.schemas import BrokerCredentialRequest, BrokerTestRequest

from ._common import ALLOWED_SERVERS, validate_broker

logger = structlog.get_logger()

router = APIRouter(prefix="/htmx/broker", tags=["broker-htmx"])


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _render_broker_form(
    request: Request,
    has_credentials: bool,
    account_id_masked: str,
    server: str,
    *,
    just_saved: bool = False,
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
            "just_saved": just_saved,
        },
    )


async def _parse_credential_form(request: Request) -> BrokerCredentialRequest:
    """Parse credential request from form data."""
    form = await request.form()
    body: dict[str, str | bool] = {k: str(v) for k, v in form.items()}
    try:
        return BrokerCredentialRequest(**body)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


async def _parse_test_form(request: Request) -> BrokerTestRequest:
    """Parse test request from form data."""
    form = await request.form()
    body: dict[str, str | bool] = {k: str(v) for k, v in form.items()}
    # HTMX sends "false"/"true" as strings; coerce to bool
    if "use_stored" in body:
        body["use_stored"] = body["use_stored"] in ("true", "True", "1")
    try:
        return BrokerTestRequest(**body)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@router.get("/status", response_class=HTMLResponse)
def get_broker_status(
    request: Request,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse:
    """Return broker form HTML partial."""
    info = store.get_masked_info(user.id, "oanda")

    has_credentials = info is not None
    account_id_masked = info.account_id_masked if info else ""
    server = info.server if info else "practice"

    return _render_broker_form(request, has_credentials, account_id_masked, server)


@router.put("/credentials", response_class=HTMLResponse)
async def save_credentials(
    request: Request,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse:
    """Save broker credentials and return updated form partial."""
    req = await _parse_credential_form(request)
    validate_broker(req.broker)
    if req.server not in ALLOWED_SERVERS:
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

    return _render_broker_form(
        request,
        info.has_credentials,
        info.account_id_masked,
        info.server,
        just_saved=True,
    )


@router.delete("/credentials", response_class=HTMLResponse)
def delete_credentials(
    request: Request,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse:
    """Remove stored broker credentials and return empty form partial."""
    store.delete(user.id, "oanda")
    logger.info("broker.credentials_deleted", broker="oanda", user_id=user.id)

    return _render_broker_form(request, False, "", "practice")


@router.post("/test", response_class=HTMLResponse)
async def test_connection(
    request: Request,
    user: User = Depends(get_current_user),
    store: FernetCredentialStore = Depends(get_credential_store),
) -> HTMLResponse:
    """Test broker connection and return status HTML partial."""
    req = await _parse_test_form(request)
    validate_broker(req.broker)

    if req.use_stored:
        try:
            creds = store.retrieve(user.id, req.broker)
        except CredentialDecryptionError:
            return _render_test_result(
                request,
                False,
                "Cannot decrypt stored credentials. The encryption key may have changed.",
            )
        if creds is None:
            return _render_test_result(
                request, False, "No stored credentials found. Save credentials first."
            )
        account_id = creds.account_id
        access_token = creds.access_token
        server = creds.server
    else:
        if not req.account_id or not req.access_token:
            return _render_test_result(request, False, "Account ID and API token are required.")
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
        return _render_test_result(request, False, str(exc))

    return _render_test_result(request, True, "Connected successfully.")


def _render_test_result(request: Request, success: bool, message: str) -> HTMLResponse:
    """Render the broker test result partial."""
    templates = _get_templates(request)
    return templates.TemplateResponse(
        request,
        "partials/broker_status.html",
        {"success": success, "message": message},
    )
