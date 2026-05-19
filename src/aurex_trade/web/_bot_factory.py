"""Bot engine factory — creates TradingEngine instances from per-user credentials.

Used by the web layer to assemble a fully-wired TradingEngine for live
(practice-only) trading. The factory does NOT start the engine — it just
wires all dependencies and returns the engine + connection tuple.
"""

from pathlib import Path

import structlog

from aurex_trade.adapters.oanda.broker import OANDABrokerAdapter
from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.adapters.oanda.market_data import OANDAMarketDataAdapter
from aurex_trade.adapters.sqlite.repository import SQLiteRepository
from aurex_trade.backtest.cli import STRATEGY_REGISTRY
from aurex_trade.config import OANDAConfig
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.engine.trading_engine import TradingEngine
from aurex_trade.ports.credential_store import CredentialStorePort

log = structlog.get_logger()

_DB_PATH = Path("data/aurex_trade.db")


def create_bot_engine(
    user_id: str,
    strategy_name: str,
    strategy_params: dict[str, int | float],
    risk_params: dict[str, int | float | bool],
    symbol: str,
    interval_seconds: int,
    credential_store: CredentialStorePort,
    *,
    granularity: str = "M1",
    db_path: Path = _DB_PATH,
) -> tuple[TradingEngine, OANDAConnection]:
    """Create a TradingEngine wired to OANDA practice accounts.

    Returns:
        Tuple of (TradingEngine, OANDAConnection). The connection satisfies
        the Disconnectable protocol and must be passed to BotSessionManager.

    Raises:
        ValueError: If credentials are missing, server is not "practice",
                    or strategy_name is not in STRATEGY_REGISTRY.
    """
    creds = credential_store.retrieve(user_id, "oanda")
    if creds is None:
        msg = (
            f"No OANDA credentials found for user {user_id}. "
            "Configure credentials in Settings > Broker before starting a bot."
        )
        raise ValueError(msg)

    if creds.server != "practice":
        msg = (
            f"Live trading is not permitted via the web interface "
            f"(server={creds.server!r}). Only practice accounts are allowed."
        )
        raise ValueError(msg)

    if strategy_name not in STRATEGY_REGISTRY:
        msg = (
            f"Unknown strategy: {strategy_name!r}. "
            f"Available: {', '.join(sorted(STRATEGY_REGISTRY.keys()))}"
        )
        raise ValueError(msg)

    config = OANDAConfig(
        access_token=creds.access_token,
        account_id=creds.account_id,
        server=creds.server,
    )
    connection = OANDAConnection(config)
    connection.connect()

    try:
        strategy = STRATEGY_REGISTRY[strategy_name](strategy_params)

        risk_engine = RiskEngine(
            max_position_size=int(risk_params.get("max_position_size", 10)),
            max_daily_loss=float(risk_params.get("max_daily_loss", 500.0)),
            max_trades_per_day=int(risk_params.get("max_trades_per_day", 10)),
            kill_switch=bool(risk_params.get("kill_switch", False)),
            require_stop_loss=bool(risk_params.get("require_stop_loss", True)),
            risk_per_trade=float(risk_params.get("risk_per_trade", 0.02)),
            max_drawdown_pct=float(risk_params.get("max_drawdown_pct", 0.20)),
            max_consecutive_losses=int(
                risk_params.get("max_consecutive_losses", 5)
            ),
        )

        broker = OANDABrokerAdapter(connection, creds.account_id)
        market_data = OANDAMarketDataAdapter(connection, creds.account_id, granularity)
        repository = SQLiteRepository(db_path)

        engine = TradingEngine(
            strategy=strategy,
            risk_engine=risk_engine,
            broker=broker,
            market_data=market_data,
            repository=repository,
            symbol=symbol,
            interval_seconds=interval_seconds,
            bar_count=strategy.min_bars,
            user_id=user_id,
        )
    except Exception:
        connection.disconnect()
        raise

    log.info(
        "bot_engine_created",
        user_id=user_id,
        strategy=strategy_name,
        symbol=symbol,
        granularity=granularity,
        interval=interval_seconds,
    )

    return engine, connection
