"""Composition root — wires adapters to ports and starts the trading engine.

This is the ONLY module that knows about concrete adapter classes.
Everything else depends only on port interfaces.
"""

import sys

import structlog

from aurex_trade.config import AppConfig
from aurex_trade.domain.enums import TradingMode
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover
from aurex_trade.engine.trading_engine import TradingEngine
from aurex_trade.logging import setup_logging


def main() -> None:
    """Entry point — load config, wire adapters, start engine."""
    config = AppConfig()

    setup_logging(log_level=config.log_level)
    log = structlog.get_logger()

    # Live trading double-gate safety
    if config.trading_mode == TradingMode.LIVE and not config.live_trading_confirmed:
        log.critical(
            "live_trading_blocked",
            reason="LIVE trading requires LIVE_TRADING_CONFIRMED=true",
        )
        sys.exit(1)

    log.info(
        "config_loaded",
        trading_mode=config.trading_mode.value,
        symbol=config.symbol,
        interval=config.interval_seconds,
    )

    # Select adapters based on trading mode
    if config.trading_mode == TradingMode.LOCAL:
        from aurex_trade.adapters.paper.broker import PaperBrokerAdapter
        from aurex_trade.adapters.sqlite.repository import SQLiteRepository

        broker = PaperBrokerAdapter()
        market_data = broker  # Paper adapter implements both ports
        repository = SQLiteRepository(db_path=config.db_path)

    elif config.trading_mode in (TradingMode.PAPER, TradingMode.LIVE):
        log.error(
            "mode_not_implemented",
            mode=config.trading_mode.value,
            hint="IBKR adapters will be available in Phase 5",
        )
        sys.exit(1)

    # Domain components (no adapter knowledge)
    strategy = SMACrossover(
        short_window=config.strategy.sma_short_window,
        long_window=config.strategy.sma_long_window,
    )
    risk_engine = RiskEngine(
        max_position_size=config.risk.max_position_size,
        max_daily_loss=config.risk.max_daily_loss,
        max_trades_per_day=config.risk.max_trades_per_day,
        kill_switch=config.risk.kill_switch,
    )

    engine = TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=market_data,
        repository=repository,
        symbol=config.symbol,
        interval_seconds=config.interval_seconds,
    )

    log.info("engine_wired", strategy=strategy.name)

    try:
        engine.run()
    except KeyboardInterrupt:
        log.info("keyboard_interrupt")
        engine.stop()
