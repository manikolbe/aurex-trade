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
from aurex_trade.domain.models import Order, Trade
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

    def run(self) -> BacktestResult:
        """Execute the full backtest and return results."""
        equity_curve: list[float] = [self._config.initial_capital]
        trade_records: list[BacktestTradeRecord] = []
        trade_pnls: list[float] = []
        bar_index = 0

        while not self._market_data.is_exhausted:
            # Update broker with current market price
            current_bar = self._market_data.current_bar
            self._broker.set_current_bar(current_bar)

            # Run one trading step
            record = self._run_step(bar_index)
            if record is not None:
                trade_records.append(record)
                # Track realized P&L from this trade's fill
                pnl = self._get_trade_pnl(record.trade, trade_records)
                trade_pnls.append(pnl)

            # Record equity after this step
            equity_curve.append(self._broker.equity)
            bar_index += 1

            # Advance to next bar
            self._market_data.advance()

        # Calculate metrics
        metrics = calculate_metrics(
            equity_curve=equity_curve,
            trade_pnls=trade_pnls,
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

        # Step 3: Risk evaluation
        position = self._repository.get_current_position(self._config.symbol)
        trades_today = self._repository.get_trades_today(self._config.symbol)
        decision = self._risk_engine.evaluate(signal, position, trades_today)
        self._repository.save_decision(decision)

        if decision.action != RiskAction.APPROVED:
            return None

        # Step 4: Place order
        side = OrderSide.BUY if signal.signal_type == SignalType.LONG else OrderSide.SELL
        order = Order(
            signal_id=signal.id,
            symbol=self._config.symbol,
            side=side,
            quantity=self._config.position_size,
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

    def _get_trade_pnl(self, trade: Trade, records: list[BacktestTradeRecord]) -> float:
        """Calculate P&L for a closing trade.

        For a sell trade (closing a long), P&L = (sell_price - avg_cost) * qty.
        For a buy that opens a position, P&L is 0 (unrealized until close).
        """
        if trade.side != OrderSide.SELL:
            return 0.0

        position = self._broker.get_positions(trade.symbol)
        if position is None:
            return 0.0

        avg_cost = position.average_cost if position.average_cost != 0 else trade.price
        return trade.quantity * (trade.price - avg_cost)
