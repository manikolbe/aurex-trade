"""Per-user strategy and risk/cost defaults API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
from aurex_trade.domain.models import User
from aurex_trade.web.auth.dependencies import get_current_user
from aurex_trade.web.dependencies import get_user_defaults_store
from aurex_trade.web.schemas import (
    AllDefaultsResponse,
    RiskDefaultsRequest,
    RiskDefaultsResponse,
    StrategyDefaultsRequest,
    StrategyDefaultsResponse,
)

router = APIRouter(prefix="/api/user-defaults", tags=["user-defaults"])


@router.get("/strategy")
def get_strategy_defaults(
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> StrategyDefaultsResponse:
    """Get all saved strategy defaults and preferred strategy."""
    return StrategyDefaultsResponse(
        preferred_strategy=store.get_preferred_strategy(user.id),
        strategies=store.get_all_strategy_defaults(user.id),
    )


@router.put("/strategy/{strategy_name}")
def save_strategy_defaults(
    strategy_name: str,
    req: StrategyDefaultsRequest,
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> StrategyDefaultsResponse:
    """Save params for a specific strategy."""
    store.save_strategy_defaults(
        user.id, strategy_name, req.params, is_preferred=req.is_preferred
    )
    return StrategyDefaultsResponse(
        preferred_strategy=store.get_preferred_strategy(user.id),
        strategies=store.get_all_strategy_defaults(user.id),
    )


@router.delete("/strategy/{strategy_name}", status_code=204)
def delete_strategy_defaults(
    strategy_name: str,
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> None:
    """Reset a strategy to app defaults."""
    store.delete_strategy_defaults(user.id, strategy_name)


@router.get("/risk")
def get_risk_defaults(
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> RiskDefaultsResponse:
    """Get saved risk/cost defaults."""
    return RiskDefaultsResponse(settings=store.get_risk_defaults(user.id))


@router.put("/risk")
def save_risk_defaults(
    req: RiskDefaultsRequest,
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> RiskDefaultsResponse:
    """Save risk/cost defaults."""
    settings: dict[str, int | float | bool] = req.model_dump()
    store.save_risk_defaults(user.id, settings)
    return RiskDefaultsResponse(settings=settings)


@router.delete("/risk", status_code=204)
def delete_risk_defaults(
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> None:
    """Reset risk settings to app defaults."""
    store.delete_risk_defaults(user.id)


@router.get("/all")
def get_all_defaults(
    user: User = Depends(get_current_user),
    store: UserDefaultsStore = Depends(get_user_defaults_store),
) -> AllDefaultsResponse:
    """Combined endpoint for form pre-population."""
    return AllDefaultsResponse(
        preferred_strategy=store.get_preferred_strategy(user.id),
        strategy_params=store.get_all_strategy_defaults(user.id),
        risk_settings=store.get_risk_defaults(user.id),
    )
