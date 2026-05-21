"""Shared helpers for auto-saving user defaults on form submit."""

from __future__ import annotations

from aurex_trade.adapters.sqlite.user_defaults_store import UserDefaultsStore
from aurex_trade.web.schemas import BacktestRequest, SweepRequest, WalkForwardRequest


def save_user_defaults(store: UserDefaultsStore, user_id: str, req: BacktestRequest) -> None:
    """Auto-save strategy params and risk/cost settings from a backtest submit."""
    store.save_strategy_defaults(user_id, req.strategy, req.params, is_preferred=True)
    store.save_risk_defaults(user_id, extract_risk_settings(req))


def save_preferred_and_risk(
    store: UserDefaultsStore,
    user_id: str,
    req: SweepRequest | WalkForwardRequest,
) -> None:
    """Auto-save preferred strategy + risk/cost from sweep/walk-forward submit."""
    store.save_strategy_defaults(user_id, req.strategy, {}, is_preferred=True)
    store.save_risk_defaults(user_id, extract_risk_settings(req))


def extract_risk_settings(
    req: BacktestRequest | SweepRequest | WalkForwardRequest,
) -> dict[str, int | float | bool | str]:
    """Extract risk/cost fields + symbol/granularity from a request."""
    return {
        "symbol": req.symbol,
        "granularity": req.granularity,
        "max_position": req.max_position,
        "max_daily_loss": req.max_daily_loss,
        "risk_per_trade": req.risk_per_trade,
        "max_drawdown_pct": req.max_drawdown_pct,
        "max_trades_per_day": req.max_trades_per_day,
        "max_consecutive_losses": req.max_consecutive_losses,
        "require_stop_loss": req.require_stop_loss,
        "capital": req.capital,
        "position_size": req.position_size,
        "spread": req.spread,
        "slippage": req.slippage,
        "commission": req.commission,
        "seed": req.seed,
    }
