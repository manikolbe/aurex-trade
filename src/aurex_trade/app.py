"""Composition root — wires adapters to ports and starts the trading engine.

This is the ONLY module that knows about concrete adapter classes.
Everything else depends only on port interfaces.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import structlog

from aurex_trade.config import AppConfig
from aurex_trade.domain.enums import TradingMode
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover
from aurex_trade.engine.trading_engine import TradingEngine
from aurex_trade.logging import setup_logging

if TYPE_CHECKING:
    from aurex_trade.adapters.oanda.connection import OANDAConnection
    from aurex_trade.ports.broker import BrokerPort
    from aurex_trade.ports.market_data import MarketDataPort
    from aurex_trade.ports.repository import RepositoryPort


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

    oanda_conn: OANDAConnection | None = None
    broker: BrokerPort
    market_data: MarketDataPort
    repository: RepositoryPort

    # Select adapters based on trading mode
    if config.trading_mode == TradingMode.LOCAL:
        from aurex_trade.adapters.paper.broker import PaperBrokerAdapter
        from aurex_trade.adapters.sqlite.repository import SQLiteRepository

        paper = PaperBrokerAdapter()
        broker = paper
        market_data = paper  # Paper adapter implements both ports
        repository = SQLiteRepository(db_path=config.db_path)

    elif config.trading_mode in (TradingMode.PAPER, TradingMode.LIVE):
        from aurex_trade.adapters.oanda.broker import OANDABrokerAdapter
        from aurex_trade.adapters.oanda.connection import (
            OANDAConnection as _OANDAConnection,
        )
        from aurex_trade.adapters.oanda.connection import OANDAConnectionError
        from aurex_trade.adapters.oanda.market_data import OANDAMarketDataAdapter
        from aurex_trade.adapters.sqlite.repository import SQLiteRepository

        if not config.oanda.access_token:
            log.critical("oanda_config_error", reason="OANDA_ACCESS_TOKEN is required")
            sys.exit(1)
        if not config.oanda.account_id:
            log.critical("oanda_config_error", reason="OANDA_ACCOUNT_ID is required")
            sys.exit(1)

        oanda_conn = _OANDAConnection(config.oanda)
        try:
            oanda_conn.connect()
        except OANDAConnectionError as exc:
            log.critical("oanda_connection_failed", error=str(exc))
            sys.exit(1)

        broker = OANDABrokerAdapter(connection=oanda_conn, account_id=config.oanda.account_id)
        market_data = OANDAMarketDataAdapter(
            connection=oanda_conn, account_id=config.oanda.account_id
        )
        repository = SQLiteRepository(db_path=config.db_path)

    # Domain components (no adapter knowledge)
    strategy = SMACrossover(
        short_window=config.strategy.sma_short_window,
        long_window=config.strategy.sma_long_window,
        atr_multiplier=config.strategy.atr_multiplier,
        atr_period=config.strategy.atr_period,
    )
    risk_engine = RiskEngine(
        max_position_size=config.risk.max_position_size,
        max_daily_loss=config.risk.max_daily_loss,
        max_trades_per_day=config.risk.max_trades_per_day,
        kill_switch=config.risk.kill_switch,
        require_stop_loss=config.risk.require_stop_loss,
        risk_per_trade=config.risk.risk_per_trade,
        max_drawdown_pct=config.risk.max_drawdown_pct,
        max_consecutive_losses=config.risk.max_consecutive_losses,
    )

    engine = TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=market_data,
        repository=repository,
        symbol=config.symbol,
        interval_seconds=config.interval_seconds,
        user_id="cli",
    )

    log.info("engine_wired", strategy=strategy.name)

    try:
        engine.run()
    except KeyboardInterrupt:
        log.info("keyboard_interrupt")
        engine.stop()
    finally:
        if oanda_conn is not None:
            oanda_conn.disconnect()
