"""Shared runner factories for backtest, sweep, and walk-forward tasks."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from aurex_trade.web.schemas import BacktestRequest, SweepRequest, WalkForwardRequest
from aurex_trade.web.tasks import TaskRegistry

if TYPE_CHECKING:
    from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
    from aurex_trade.domain.models import BarData

_logger = logging.getLogger(__name__)


def _ensure_data_available(
    data_store: HistoricalDataStore,
    symbol: str,
    granularity: str,
    start: datetime | None,
    end: datetime | None,
    task_id: UUID | None = None,
    registry: TaskRegistry | None = None,
) -> list[BarData]:
    """Load historical bars, auto-downloading from OANDA if missing.

    Returns bars for the requested range. Downloads from OANDA when the local
    CSV is missing or does not fully cover the requested date range.

    Raises:
        FileNotFoundError: If data cannot be obtained (no dates or download empty).
        ValueError: If OANDA credentials are not configured.
    """
    # Attempt to load existing data
    bars: list[BarData] = []
    with contextlib.suppress(FileNotFoundError):
        bars = data_store.load_bars(symbol, granularity, start, end)

    # If we have any bars in the requested range, use them.
    # Don't second-guess market-hours gaps (weekends, holidays).
    if bars:
        return bars

    # Cannot download without concrete date range
    if start is None or end is None:
        msg = f"No data found for {symbol} ({granularity})"
        raise FileNotFoundError(msg)

    # Check OANDA credentials
    from aurex_trade.config import OANDAConfig

    oanda_config = OANDAConfig()
    if not oanda_config.access_token or not oanda_config.account_id:
        msg = (
            "OANDA credentials not configured. "
            "Set OANDA_ACCESS_TOKEN and OANDA_ACCOUNT_ID in your .env file, "
            "or configure them in Settings."
        )
        raise ValueError(msg)

    # Update task progress
    if task_id is not None and registry is not None:
        registry.update_message(
            task_id, f"Downloading {symbol} ({granularity}) data..."
        )

    # Download from OANDA
    from aurex_trade.adapters.oanda.connection import OANDAConnection
    from aurex_trade.adapters.oanda.downloader import OANDAHistoricalDownloader

    connection = OANDAConnection(oanda_config)
    try:
        connection.connect()
        downloader = OANDAHistoricalDownloader(connection, data_store)
        count = downloader.download(symbol, granularity, start, end)
        _logger.info("Downloaded %d candles for %s (%s)", count, symbol, granularity)
    finally:
        connection.disconnect()

    # Reload bars after download
    bars = data_store.load_bars(symbol, granularity, start, end)
    if not bars:
        msg = f"No data found for {symbol} ({granularity}) after download"
        raise FileNotFoundError(msg)

    return bars


def create_backtest_runner(
    req: BacktestRequest,
    task_id: UUID | None = None,
    registry: TaskRegistry | None = None,
) -> Callable[[], object]:
    """Create a callable that runs a single backtest with the given parameters."""

    def run() -> object:
        from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
        from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
        from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
        from aurex_trade.adapters.memory.repository import InMemoryRepository
        from aurex_trade.backtest.cli import (
            PARAM_VALIDATORS,
            STRATEGY_METADATA,
            STRATEGY_REGISTRY,
        )
        from aurex_trade.backtest.config import BacktestConfig
        from aurex_trade.backtest.runner import BacktestRunner
        from aurex_trade.domain.risk.engine import RiskEngine

        if req.strategy not in STRATEGY_REGISTRY:
            msg = f"Unknown strategy: {req.strategy}"
            raise ValueError(msg)

        # Resolve params: fill defaults from metadata if not provided
        params = dict(req.params)
        if not params:
            meta = STRATEGY_METADATA[req.strategy]()
            params = {p.key: p.default for p in meta.params}

        # Validate params
        validator = PARAM_VALIDATORS.get(req.strategy)
        if validator and not validator(params):
            msg = f"Invalid parameters for {req.strategy}: {params}"
            raise ValueError(msg)

        strategy = STRATEGY_REGISTRY[req.strategy](params)

        config = BacktestConfig(
            symbol=req.symbol,
            granularity=req.granularity,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.capital,
            position_size=req.position_size,
            spread_pips=req.spread,
            slippage_pips=req.slippage,
            commission_per_trade=req.commission,
            deterministic_seed=req.seed,
            data_dir=Path("data/historical"),
            bar_count=strategy.min_bars,
        )

        data_store = HistoricalDataStore(config.data_dir)
        start = (
            datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.start_date
            else None
        )
        end = (
            datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.end_date
            else None
        )
        bars = _ensure_data_available(
            data_store, config.symbol, config.granularity, start, end,
            task_id=task_id, registry=registry,
        )

        if task_id is not None and registry is not None:
            registry.update_message(task_id, "Running backtest...")

        risk_engine = RiskEngine(
            max_position_size=req.max_position,
            max_daily_loss=req.max_daily_loss,
            max_trades_per_day=req.max_trades_per_day,
            require_stop_loss=req.require_stop_loss,
            risk_per_trade=req.risk_per_trade,
            max_drawdown_pct=req.max_drawdown_pct,
            max_consecutive_losses=req.max_consecutive_losses,
        )
        market_data = HistoricalMarketDataAdapter(bars, config.bar_count)
        broker = SimulatedBrokerAdapter(
            initial_capital=config.initial_capital,
            spread=config.spread_pips,
            slippage=config.slippage_pips,
            commission_per_trade=config.commission_per_trade,
            seed=config.deterministic_seed,
        )
        repository = InMemoryRepository()

        runner = BacktestRunner(
            strategy=strategy,
            risk_engine=risk_engine,
            market_data=market_data,
            broker=broker,
            repository=repository,
            config=config,
        )
        result = runner.run()

        # Attach strategy parameters (runner leaves them empty)
        from aurex_trade.backtest.results import BacktestResult

        return BacktestResult(
            metrics=result.metrics,
            equity_curve=result.equity_curve,
            trades=result.trades,
            strategy_name=result.strategy_name,
            symbol=result.symbol,
            start_date=result.start_date,
            end_date=result.end_date,
            parameters={k: str(v) for k, v in params.items()},
        )

    return run


def create_sweep_runner(
    req: SweepRequest,
    task_id: UUID | None = None,
    registry: TaskRegistry | None = None,
) -> Callable[[], object]:
    """Create a callable that runs a parameter sweep with the given parameters."""

    def run() -> object:
        from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
        from aurex_trade.backtest.cli import PARAM_VALIDATORS, STRATEGY_REGISTRY
        from aurex_trade.backtest.config import BacktestConfig
        from aurex_trade.backtest.sweep import ParameterSweep
        from aurex_trade.domain.risk.engine import RiskEngine

        if req.strategy not in STRATEGY_REGISTRY:
            msg = f"Unknown strategy: {req.strategy}"
            raise ValueError(msg)

        config = BacktestConfig(
            symbol=req.symbol,
            granularity=req.granularity,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.capital,
            position_size=req.position_size,
            spread_pips=req.spread,
            slippage_pips=req.slippage,
            commission_per_trade=req.commission,
            deterministic_seed=req.seed,
            data_dir=Path("data/historical"),
        )

        data_store = HistoricalDataStore(config.data_dir)
        start = (
            datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.start_date
            else None
        )
        end = (
            datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.end_date
            else None
        )
        bars = _ensure_data_available(
            data_store, config.symbol, config.granularity, start, end,
            task_id=task_id, registry=registry,
        )

        if task_id is not None and registry is not None:
            registry.update_message(task_id, "Running parameter sweep...")

        risk_engine = RiskEngine(
            max_position_size=req.max_position,
            max_daily_loss=req.max_daily_loss,
            max_trades_per_day=req.max_trades_per_day,
            require_stop_loss=req.require_stop_loss,
            risk_per_trade=req.risk_per_trade,
            max_drawdown_pct=req.max_drawdown_pct,
            max_consecutive_losses=req.max_consecutive_losses,
        )

        sweep = ParameterSweep(
            strategy_factory=STRATEGY_REGISTRY[req.strategy],
            param_grid=req.params,
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            rank_by=req.rank_by,
            param_validator=PARAM_VALIDATORS.get(req.strategy),
        )
        return sweep.run()

    return run


def create_walk_forward_runner(
    req: WalkForwardRequest,
    task_id: UUID | None = None,
    registry: TaskRegistry | None = None,
) -> Callable[[], object]:
    """Create a callable that runs walk-forward validation with the given parameters."""

    def run() -> object:
        from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
        from aurex_trade.backtest.cli import PARAM_VALIDATORS, STRATEGY_REGISTRY
        from aurex_trade.backtest.config import BacktestConfig
        from aurex_trade.backtest.walk_forward import WalkForwardValidator
        from aurex_trade.domain.risk.engine import RiskEngine

        if req.strategy not in STRATEGY_REGISTRY:
            msg = f"Unknown strategy: {req.strategy}"
            raise ValueError(msg)

        config = BacktestConfig(
            symbol=req.symbol,
            granularity=req.granularity,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.capital,
            position_size=req.position_size,
            spread_pips=req.spread,
            slippage_pips=req.slippage,
            commission_per_trade=req.commission,
            deterministic_seed=req.seed,
            data_dir=Path("data/historical"),
        )

        data_store = HistoricalDataStore(config.data_dir)
        start = (
            datetime.strptime(config.start_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.start_date
            else None
        )
        end = (
            datetime.strptime(config.end_date, "%Y-%m-%d").replace(tzinfo=UTC)
            if config.end_date
            else None
        )
        bars = _ensure_data_available(
            data_store, config.symbol, config.granularity, start, end,
            task_id=task_id, registry=registry,
        )

        if task_id is not None and registry is not None:
            registry.update_message(task_id, "Running walk-forward validation...")

        risk_engine = RiskEngine(
            max_position_size=req.max_position,
            max_daily_loss=req.max_daily_loss,
            max_trades_per_day=req.max_trades_per_day,
            require_stop_loss=req.require_stop_loss,
            risk_per_trade=req.risk_per_trade,
            max_drawdown_pct=req.max_drawdown_pct,
            max_consecutive_losses=req.max_consecutive_losses,
        )

        validator = WalkForwardValidator(
            strategy_factory=STRATEGY_REGISTRY[req.strategy],
            param_grid=req.params,
            bars=bars,
            config=config,
            risk_engine=risk_engine,
            train_bars=req.train_bars,
            test_bars=req.test_bars,
            rank_by=req.rank_by,
            param_validator=PARAM_VALIDATORS.get(req.strategy),
        )
        return validator.run()

    return run
