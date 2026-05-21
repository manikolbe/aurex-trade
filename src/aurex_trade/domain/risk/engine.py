"""Risk engine — mandatory gate between strategy signals and order execution.

Every signal must pass through evaluate(). No trade can bypass this check.
Rules are evaluated in priority order; the first rejection wins.
"""

import structlog

from aurex_trade.domain.enums import RiskAction
from aurex_trade.domain.models import AccountState, Position, RiskDecision, Signal, Trade

log = structlog.get_logger()


class RiskEngine:
    """Evaluates trading signals against risk rules.

    Rules (checked in order):
    1. Kill switch — if enabled, reject everything immediately
    2. Stop-loss enforcement — reject if signal has no stop_loss (configurable)
    3. Max drawdown — reject if equity drawdown from peak exceeds threshold
    4. Consecutive losses — reject if last N trades were all losers
    5. Max position size — reject if resulting position would exceed limit
    6. Max daily loss — reject if daily P&L is below threshold
    7. Trade frequency — reject if trade count exceeds daily limit
    """

    def __init__(
        self,
        max_position_size: int,
        max_daily_loss: float,
        max_trades_per_day: int,
        kill_switch: bool = False,
        require_stop_loss: bool = True,
        risk_per_trade: float = 0.02,
        max_drawdown_pct: float = 0.20,
        max_consecutive_losses: int = 5,
    ) -> None:
        self._max_position_size = max_position_size
        self._max_daily_loss = max_daily_loss
        self._max_trades_per_day = max_trades_per_day
        self._kill_switch = kill_switch
        self._require_stop_loss = require_stop_loss
        self._risk_per_trade = risk_per_trade
        self._max_drawdown_pct = max_drawdown_pct
        self._max_consecutive_losses = max_consecutive_losses

    @property
    def kill_switch(self) -> bool:
        """Whether the kill switch is currently active."""
        return self._kill_switch

    @kill_switch.setter
    def kill_switch(self, value: bool) -> None:
        self._kill_switch = value
        log.info("kill_switch_toggled", active=value)

    def evaluate(
        self,
        signal: Signal,
        position: Position | None,
        trades_today: list[Trade],
        account_state: AccountState | None = None,
        recent_trade_pnls: list[float] | None = None,
    ) -> RiskDecision:
        """Evaluate a signal against all risk rules.

        Args:
            signal: The trading signal to evaluate.
            position: Current position for the symbol (None if no position).
            trades_today: All trades executed today for the symbol.
            account_state: Current equity and peak equity (for drawdown checks).
            recent_trade_pnls: Recent per-trade P&Ls, most recent last
                (for consecutive loss detection).

        Returns:
            RiskDecision with APPROVED, REJECTED, or KILL_SWITCH action.
        """
        # Rule 1: Kill switch
        if self._kill_switch:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.KILL_SWITCH,
                reason="Kill switch is active — all trading halted",
            )

        # Rule 2: Stop-loss enforcement
        if self._require_stop_loss and signal.stop_loss is None:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.REJECTED,
                reason="Signal rejected — no stop-loss provided (require_stop_loss is enabled)",
            )

        # Rule 3: Max drawdown circuit breaker
        if account_state is not None and account_state.peak_equity > 0:
            drawdown_pct = (
                account_state.peak_equity - account_state.equity
            ) / account_state.peak_equity
            if drawdown_pct >= self._max_drawdown_pct:
                return RiskDecision(
                    signal_id=signal.id,
                    action=RiskAction.REJECTED,
                    reason=f"Max drawdown breaker — current drawdown "
                    f"{drawdown_pct:.1%} exceeds limit {self._max_drawdown_pct:.1%}",
                )

        # Rule 4: Consecutive loss pause
        if recent_trade_pnls is not None:
            n = self._max_consecutive_losses
            tail = recent_trade_pnls[-n:]
            if len(tail) >= n and all(pnl < 0 for pnl in tail):
                return RiskDecision(
                    signal_id=signal.id,
                    action=RiskAction.REJECTED,
                    reason=f"Consecutive loss pause — last {n} trades were all losses",
                )

        # Rule 5: Max position size
        current_qty = position.quantity if position else 0.0
        if abs(current_qty) >= self._max_position_size:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.REJECTED,
                reason=f"Position size {abs(current_qty)} "
                f"already at or exceeds max {self._max_position_size}",
            )

        # Rule 6: Max daily loss
        daily_pnl = 0.0
        if position:
            daily_pnl = position.realized_pnl + position.unrealized_pnl
        log.debug(
            "risk_pnl_breakdown",
            realized_pnl=position.realized_pnl if position else 0.0,
            unrealized_pnl=position.unrealized_pnl if position else 0.0,
            daily_pnl=daily_pnl,
            max_daily_loss=self._max_daily_loss,
            position_qty=position.quantity if position else 0.0,
            position_avg_cost=position.average_cost if position else 0.0,
        )
        if daily_pnl <= -self._max_daily_loss:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.REJECTED,
                reason=f"Daily P&L {daily_pnl:.2f} "
                f"exceeds max loss limit -{self._max_daily_loss:.2f}",
            )

        # Rule 7: Trade frequency
        if len(trades_today) >= self._max_trades_per_day:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.REJECTED,
                reason=f"Already executed {len(trades_today)} trades today "
                f"(max {self._max_trades_per_day})",
            )

        return RiskDecision(
            signal_id=signal.id,
            action=RiskAction.APPROVED,
            reason="All risk checks passed",
        )

    def calculate_position_size(
        self,
        signal: Signal,
        account_state: AccountState,
        entry_price: float,
    ) -> float:
        """Calculate position size based on risk per trade and stop distance.

        Formula: units = (equity * risk_per_trade) / stop_distance
        Capped at max_position_size.

        Returns 0.0 if position cannot be sized (no stop-loss or zero distance).
        """
        if signal.stop_loss is None:
            return 0.0

        stop_distance = abs(entry_price - signal.stop_loss)
        if stop_distance == 0.0:
            return 0.0

        risk_amount = account_state.equity * self._risk_per_trade
        units = risk_amount / stop_distance

        return min(units, float(self._max_position_size))
