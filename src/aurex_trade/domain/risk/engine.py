"""Risk engine — mandatory gate between strategy signals and order execution.

Every signal must pass through evaluate(). No trade can bypass this check.
Rules are evaluated in priority order; the first rejection wins.
"""

from aurex_trade.domain.enums import RiskAction
from aurex_trade.domain.models import Position, RiskDecision, Signal, Trade


class RiskEngine:
    """Evaluates trading signals against risk rules.

    Rules (checked in order):
    1. Kill switch — if enabled, reject everything immediately
    2. Max position size — reject if resulting position would exceed limit
    3. Max daily loss — reject if daily P&L is below threshold
    4. Trade frequency — reject if trade count exceeds daily limit
    """

    def __init__(
        self,
        max_position_size: int,
        max_daily_loss: float,
        max_trades_per_day: int,
        kill_switch: bool = False,
    ) -> None:
        self._max_position_size = max_position_size
        self._max_daily_loss = max_daily_loss
        self._max_trades_per_day = max_trades_per_day
        self._kill_switch = kill_switch

    def evaluate(
        self,
        signal: Signal,
        position: Position | None,
        trades_today: list[Trade],
    ) -> RiskDecision:
        """Evaluate a signal against all risk rules.

        Args:
            signal: The trading signal to evaluate.
            position: Current position for the symbol (None if no position).
            trades_today: All trades executed today for the symbol.

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

        # Rule 2: Max position size
        current_qty = position.quantity if position else 0.0
        if abs(current_qty) >= self._max_position_size:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.REJECTED,
                reason=f"Position size {abs(current_qty)} "
                f"already at or exceeds max {self._max_position_size}",
            )

        # Rule 3: Max daily loss
        realized = sum(self._trade_pnl(t) for t in trades_today)
        unrealized = position.unrealized_pnl if position else 0.0
        daily_pnl = realized + unrealized
        if daily_pnl <= -self._max_daily_loss:
            return RiskDecision(
                signal_id=signal.id,
                action=RiskAction.REJECTED,
                reason=f"Daily P&L {daily_pnl:.2f} "
                f"exceeds max loss limit -{self._max_daily_loss:.2f}",
            )

        # Rule 4: Trade frequency
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

    @staticmethod
    def _trade_pnl(trade: Trade) -> float:
        """Estimate P&L contribution of a single trade.

        Buys are negative cash flow, sells are positive.
        Commission is always a cost.
        """
        sign = -1.0 if trade.side.value == "buy" else 1.0
        return sign * trade.quantity * trade.price - trade.commission
