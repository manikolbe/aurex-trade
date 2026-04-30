"""Backtest runner — orchestrates historical replay through strategy and risk.

Depends ONLY on port interfaces, domain types, and shared metrics.
Never imports concrete adapters.
"""

from __future__ import annotations

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.results import BacktestResult, BacktestTradeRecord
from aurex_trade.domain.enums import OrderSide, RiskAction, SignalType
from aurex_trade.domain.models import AccountState, Order
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy
from aurex_trade.metrics import calculate_metrics
from aurex_trade.ports.repository import RepositoryPort


class BacktestRunner:
    """Replays historical bars through a strategy, producing BacktestResult.

    Mirrors TradingEngine._run_cycle() logic:
    bars -> signal -> risk -> order -> position update -> record equity.
    """

    def __init__(
        self,
        strategy: Strategy,
        risk_engine: RiskEngine,
        market_data: HistoricalMarketDataAdapter,
        broker: SimulatedBrokerAdapter,
        repository: RepositoryPort,
        config: BacktestConfig,
    ) -> None:
        self._strategy = strategy
        self._risk_engine = risk_engine
        self._market_data = market_data
        self._broker = broker
        self._repository = repository
        self._config = config
        self._peak_equity: float = config.initial_capital
        self._trade_pnls: list[float] = []

    def run(self) -> BacktestResult:
        """Execute the full backtest and return results."""
        equity_curve: list[float] = [self._config.initial_capital]
        trade_records: list[BacktestTradeRecord] = []
        bar_index = 0

        while not self._market_data.is_exhausted:
            # Update broker with current market price
            current_bar = self._market_data.current_bar
            self._broker.set_current_bar(current_bar)

            # Run one trading step — track realized P&L delta
            prev_realized = self._get_realized_pnl()
            record = self._run_step(bar_index)
            if record is not None:
                trade_records.append(record)
                new_realized = self._get_realized_pnl()
                pnl = new_realized - prev_realized
                if pnl != 0.0:
                    self._trade_pnls.append(pnl)

            # Record equity after this step and update peak
            current_equity = self._broker.equity
            if current_equity > self._peak_equity:
                self._peak_equity = current_equity
            equity_curve.append(current_equity)
            bar_index += 1

            # Advance to next bar
            self._market_data.advance()

        # Calculate metrics
        metrics = calculate_metrics(
            equity_curve=equity_curve,
            trade_pnls=self._trade_pnls,
            initial_capital=self._config.initial_capital,
            total_commission=self._broker.total_commission,
        )

        # Determine date range from data
        bars = self._market_data.get_latest_bars(self._config.symbol, 1)
        start_date = bars[0].timestamp if bars else None

        return BacktestResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trades=trade_records,
            strategy_name=self._strategy.name,
            symbol=self._config.symbol,
            start_date=start_date,
            end_date=current_bar.timestamp if bar_index > 0 else None,
            parameters={},
        )

    def _run_step(self, bar_index: int) -> BacktestTradeRecord | None:
        """Execute one trading step. Returns a trade record if a trade was placed."""
        # Step 1: Get bars for strategy
        bars = self._market_data.get_latest_bars(
            self._config.symbol, self._config.bar_count
        )
        if not bars:
            return None

        # Step 2: Generate signal
        signal = self._strategy.generate(bars)
        if signal is None:
            return None

        self._repository.save_signal(signal)

        # Step 3: Risk evaluation with account state
        position = self._repository.get_current_position(self._config.symbol)
        trades_today = self._repository.get_trades_today(self._config.symbol)

        current_equity = self._broker.equity
        account_state = AccountState(
            equity=current_equity, peak_equity=self._peak_equity
        )

        decision = self._risk_engine.evaluate(
            signal,
            position,
            trades_today,
            account_state=account_state,
            recent_trade_pnls=self._trade_pnls,
        )
        self._repository.save_decision(decision)

        if decision.action != RiskAction.APPROVED:
            return None

        # Step 4: Calculate position size and place order
        side = OrderSide.BUY if signal.signal_type == SignalType.LONG else OrderSide.SELL
        entry_price = bars[-1].close

        quantity = self._risk_engine.calculate_position_size(
            signal, account_state, entry_price
        )
        if quantity <= 0.0:
            quantity = self._config.position_size

        # Cap at configured max
        quantity = min(quantity, self._config.position_size)

        order = Order(
            signal_id=signal.id,
            symbol=self._config.symbol,
            side=side,
            quantity=quantity,
            stop_loss=signal.stop_loss,
        )

        trade = self._broker.place_order(order)
        self._repository.save_trade(trade)

        # Step 5: Update position
        updated_position = self._broker.get_positions(self._config.symbol)
        if updated_position:
            self._repository.save_position(updated_position)

        return BacktestTradeRecord(
            trade=trade,
            signal=signal,
            bar_index=bar_index,
            equity_after=self._broker.equity,
        )

    def _get_realized_pnl(self) -> float:
        """Get the broker's current total realized P&L."""
        position = self._broker.get_positions(self._config.symbol)
        return position.realized_pnl if position else 0.0
