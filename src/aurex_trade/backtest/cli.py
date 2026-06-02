"""Backtest CLI — subcommands for downloading data, running backtests, sweeps."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.adapters.oanda.downloader import OANDAHistoricalDownloader
from aurex_trade.adapters.sqlite.market_data_store import SQLiteMarketDataStore
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.results import BacktestResult, SweepResult, WalkForwardResult
from aurex_trade.config import OANDAConfig
from aurex_trade.domain.models import BarData
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.base import Strategy, StrategyMetadata
from aurex_trade.domain.strategy.ciby_hedged_grid import CibyHedgedGridStrategy
from aurex_trade.domain.strategy.rsi_mean_reversion import RSIMeanReversion
from aurex_trade.domain.strategy.simple_grid import SimpleGridStrategy
from aurex_trade.domain.strategy.sma_crossover import SMACrossover
from aurex_trade.metrics import RANKABLE_METRICS

# Strategy factory registry — maps name to (params → Strategy) callable
STRATEGY_REGISTRY: dict[str, Callable[[dict[str, int | float]], Strategy]] = {
    "sma_crossover": lambda p: SMACrossover(
        short_window=int(p["short_window"]),
        long_window=int(p["long_window"]),
        atr_multiplier=float(p.get("atr_multiplier", 2.0)),
        atr_period=int(p.get("atr_period", 14)),
        reward_ratio=float(p.get("reward_ratio", 2.0)),
    ),
    "rsi_mean_reversion": lambda p: RSIMeanReversion(
        period=int(p.get("period", 14)),
        overbought=int(p.get("overbought", 70)),
        oversold=int(p.get("oversold", 30)),
        atr_multiplier=float(p.get("atr_multiplier", 2.0)),
        atr_period=int(p.get("atr_period", 14)),
        reward_ratio=float(p.get("reward_ratio", 1.5)),
    ),
    "simple_grid": lambda p: SimpleGridStrategy(
        grid_spacing=float(p.get("grid_spacing", 10.0)),
        max_levels=int(p.get("max_levels", 6)),
        stop_distance=float(p.get("stop_distance", 30.0)),
        num_levels_above=int(p.get("num_levels_above", 3)),
        num_levels_below=int(p.get("num_levels_below", 3)),
        reward_ratio=float(p.get("reward_ratio", 1.0)),
    ),
    "ciby_hedged_grid": lambda p: CibyHedgedGridStrategy(
        grid_spacing=float(p.get("grid_spacing", 10.0)),
        grid_units=float(p.get("grid_units", 10.0)),
        session_profit_target=float(p.get("session_profit_target", 100.0)),
        session_loss_limit=float(p.get("session_loss_limit", 50.0)),
        daily_loss_limit=float(p.get("daily_loss_limit", 200.0)),
    ),
}

# Per-strategy validators — filters out invalid param combos
PARAM_VALIDATORS: dict[str, Callable[[dict[str, int | float]], bool]] = {
    "sma_crossover": lambda p: p["short_window"] < p["long_window"],
    "rsi_mean_reversion": lambda p: (
        p.get("period", 14) > 0 and 0 < p.get("oversold", 30) < p.get("overbought", 70) < 100
    ),
    "simple_grid": lambda p: (
        p.get("grid_spacing", 10.0) > 0
        and p.get("max_levels", 6) >= 2
        and p.get("stop_distance", 30.0) > 0
        and p.get("num_levels_above", 3) >= 1
        and p.get("num_levels_below", 3) >= 1
    ),
    "ciby_hedged_grid": lambda p: (
        p.get("grid_spacing", 10.0) > 0
        and p.get("grid_units", 10.0) > 0
        and p.get("session_profit_target", 100.0) > 0
        and p.get("session_loss_limit", 50.0) > 0
        and p.get("daily_loss_limit", 200.0) > 0
    ),
}

# Maps strategy names to their metadata accessor
STRATEGY_METADATA: dict[str, Callable[[], StrategyMetadata]] = {
    "sma_crossover": SMACrossover.metadata,
    "rsi_mean_reversion": RSIMeanReversion.metadata,
    "simple_grid": SimpleGridStrategy.metadata,
    "ciby_hedged_grid": CibyHedgedGridStrategy.metadata,
}


def get_strategy_metadata(name: str) -> StrategyMetadata:
    """Retrieve metadata for a registered strategy by name.

    Raises KeyError if the strategy name is not registered.
    """
    return STRATEGY_METADATA[name]()


def main() -> None:
    """Entry point for the backtest CLI."""
    parser = argparse.ArgumentParser(
        prog="aurex_trade.backtest",
        description="AurexTrade Backtesting Framework",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download-data subcommand
    dl_parser = subparsers.add_parser(
        "download-data", help="Download historical candles from OANDA"
    )
    dl_parser.add_argument("--symbol", default="XAU_USD", help="Instrument symbol")
    dl_parser.add_argument("--granularity", default="M1", help="Candle granularity")
    dl_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    dl_parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    dl_parser.add_argument("--db-path", default="data/aurex_trade.db", help="SQLite database path")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run a backtest")
    run_parser.add_argument(
        "--strategy",
        default="sma_crossover",
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategy to use",
    )
    run_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Strategy parameter (e.g. --param short_window=10)",
    )
    run_parser.add_argument("--symbol", default="XAU_USD", help="Instrument symbol")
    run_parser.add_argument("--granularity", default="M1", help="Bar granularity")
    run_parser.add_argument("--start", default="", help="Start date filter (YYYY-MM-DD)")
    run_parser.add_argument("--end", default="", help="End date filter (YYYY-MM-DD)")
    run_parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital")
    run_parser.add_argument("--position-size", type=float, default=1.0, help="Units per trade")
    run_parser.add_argument("--spread", type=float, default=1.5, help="Spread in price units")
    run_parser.add_argument("--slippage", type=float, default=0.5, help="Slippage in price units")
    run_parser.add_argument("--commission", type=float, default=0.0, help="Commission per trade")
    run_parser.add_argument("--seed", type=int, default=42, help="Random seed")
    run_parser.add_argument(
        "--db-path", default="data/aurex_trade.db", help="SQLite database path"
    )
    run_parser.add_argument(
        "--max-position", type=int, default=10, help="Max position size (risk)"
    )
    run_parser.add_argument(
        "--max-daily-loss", type=float, default=500.0, help="Max daily loss (risk)"
    )
    run_parser.add_argument(
        "--max-trades-per-day", type=int, default=100, help="Max trades per day (risk)"
    )
    run_parser.add_argument(
        "--risk-per-trade", type=float, default=0.02, help="Risk per trade as fraction"
    )
    run_parser.add_argument(
        "--max-drawdown-pct", type=float, default=0.20, help="Max drawdown from peak"
    )
    run_parser.add_argument(
        "--max-consecutive-losses", type=int, default=5, help="Max consecutive losses"
    )
    run_parser.add_argument(
        "--no-require-stop-loss",
        action="store_true",
        help="Disable stop-loss requirement",
    )

    # sweep subcommand
    sweep_parser = subparsers.add_parser("sweep", help="Run parameter sweep (grid search)")
    sweep_parser.add_argument(
        "--strategy",
        default="sma_crossover",
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategy to sweep",
    )
    sweep_parser.add_argument(
        "--param",
        action="append",
        required=True,
        metavar="KEY=V1,V2,...",
        help="Parameter grid (e.g. --param short_window=5,10,20)",
    )
    sweep_parser.add_argument("--symbol", default="XAU_USD")
    sweep_parser.add_argument("--granularity", default="M1")
    sweep_parser.add_argument("--start", default="")
    sweep_parser.add_argument("--end", default="")
    sweep_parser.add_argument("--capital", type=float, default=100_000.0)
    sweep_parser.add_argument("--position-size", type=float, default=1.0)
    sweep_parser.add_argument("--spread", type=float, default=0.6)
    sweep_parser.add_argument("--slippage", type=float, default=0.2)
    sweep_parser.add_argument("--commission", type=float, default=0.0)
    sweep_parser.add_argument("--seed", type=int, default=42)
    sweep_parser.add_argument("--db-path", default="data/aurex_trade.db")
    sweep_parser.add_argument("--max-position", type=int, default=10)
    sweep_parser.add_argument("--max-daily-loss", type=float, default=5000.0)
    sweep_parser.add_argument("--max-trades-per-day", type=int, default=100)
    sweep_parser.add_argument("--risk-per-trade", type=float, default=0.02)
    sweep_parser.add_argument("--max-drawdown-pct", type=float, default=0.20)
    sweep_parser.add_argument("--max-consecutive-losses", type=int, default=5)
    sweep_parser.add_argument("--no-require-stop-loss", action="store_true")
    sweep_parser.add_argument(
        "--rank-by",
        default="sharpe_ratio",
        choices=RANKABLE_METRICS,
        help="Metric to rank by",
    )

    # walk-forward subcommand
    wf_parser = subparsers.add_parser("walk-forward", help="Run walk-forward validation")
    wf_parser.add_argument(
        "--strategy",
        default="sma_crossover",
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategy to validate",
    )
    wf_parser.add_argument(
        "--param",
        action="append",
        required=True,
        metavar="KEY=V1,V2,...",
        help="Parameter grid (e.g. --param short_window=5,10,20)",
    )
    wf_parser.add_argument("--symbol", default="XAU_USD")
    wf_parser.add_argument("--granularity", default="M1")
    wf_parser.add_argument("--start", default="")
    wf_parser.add_argument("--end", default="")
    wf_parser.add_argument("--capital", type=float, default=100_000.0)
    wf_parser.add_argument("--position-size", type=float, default=1.0)
    wf_parser.add_argument("--spread", type=float, default=0.6)
    wf_parser.add_argument("--slippage", type=float, default=0.2)
    wf_parser.add_argument("--commission", type=float, default=0.0)
    wf_parser.add_argument("--seed", type=int, default=42)
    wf_parser.add_argument("--db-path", default="data/aurex_trade.db")
    wf_parser.add_argument("--max-position", type=int, default=10)
    wf_parser.add_argument("--max-daily-loss", type=float, default=5000.0)
    wf_parser.add_argument("--max-trades-per-day", type=int, default=100)
    wf_parser.add_argument("--risk-per-trade", type=float, default=0.02)
    wf_parser.add_argument("--max-drawdown-pct", type=float, default=0.20)
    wf_parser.add_argument("--max-consecutive-losses", type=int, default=5)
    wf_parser.add_argument("--no-require-stop-loss", action="store_true")
    wf_parser.add_argument(
        "--rank-by",
        default="sharpe_ratio",
        choices=RANKABLE_METRICS,
        help="Metric to rank by",
    )
    wf_parser.add_argument(
        "--train-bars",
        type=int,
        default=7200,
        help="Bars per training window (default 7200 = 1 week M1)",
    )
    wf_parser.add_argument(
        "--test-bars",
        type=int,
        default=7200,
        help="Bars per test window (default 7200 = 1 week M1)",
    )

    args = parser.parse_args()

    if args.command == "download-data":
        _cmd_download_data(args)
    elif args.command == "run":
        _cmd_run(args)
    elif args.command == "sweep":
        _cmd_sweep(args)
    elif args.command == "walk-forward":
        _cmd_walk_forward(args)


def _cmd_download_data(args: argparse.Namespace) -> None:
    """Execute the download-data command."""
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)

    oanda_config = OANDAConfig()
    connection = OANDAConnection(oanda_config)
    connection.connect()

    data_store = SQLiteMarketDataStore(Path(args.db_path))
    downloader = OANDAHistoricalDownloader(connection, data_store)

    try:
        count = downloader.download(args.symbol, args.granularity, start, end)
        print(f"Downloaded {count} candles for {args.symbol} ({args.granularity})")
        print(f"Stored in: {args.db_path}")
    finally:
        connection.disconnect()
        data_store.close()


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the run command."""
    from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
    from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
    from aurex_trade.adapters.memory.repository import InMemoryRepository
    from aurex_trade.backtest.runner import BacktestRunner

    strategy_name: str = args.strategy
    if strategy_name not in STRATEGY_REGISTRY:
        print(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    params = _parse_params(args.param) if args.param else _default_params(strategy_name)

    # Validate params (same check as sweep/walk-forward)
    validator = PARAM_VALIDATORS.get(strategy_name)
    if validator and not validator(params):
        print(f"Invalid parameters for {strategy_name}: {params}")
        sys.exit(1)

    # Construct strategy first to get min_bars
    strategy = STRATEGY_REGISTRY[strategy_name](params)

    config = BacktestConfig(
        symbol=args.symbol,
        granularity=args.granularity,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size=args.position_size,
        spread_pips=args.spread,
        slippage_pips=args.slippage,
        commission_per_trade=args.commission,
        deterministic_seed=args.seed,
        bar_count=strategy.min_bars,
    )

    bars = _load_bars(config, Path(args.db_path))

    # Grid strategies manage their own risk (session/daily limits) — disable engine
    is_grid_strategy = hasattr(strategy, "report_fill")
    risk_engine = RiskEngine(
        max_position_size=args.max_position,
        max_daily_loss=args.max_daily_loss,
        max_trades_per_day=args.max_trades_per_day,
        require_stop_loss=not args.no_require_stop_loss,
        risk_per_trade=args.risk_per_trade,
        max_drawdown_pct=args.max_drawdown_pct,
        max_consecutive_losses=args.max_consecutive_losses,
        enabled=not is_grid_strategy,
    )
    market_data = HistoricalMarketDataAdapter(bars, config.bar_count)
    broker = SimulatedBrokerAdapter(
        initial_capital=config.initial_capital,
        spread=config.spread_pips,
        slippage=config.slippage_pips,
        commission_per_trade=config.commission_per_trade,
        seed=config.deterministic_seed,
        grid_mode=is_grid_strategy,
    )
    repository = InMemoryRepository()

    # Run backtest
    runner = BacktestRunner(
        strategy=strategy,
        risk_engine=risk_engine,
        market_data=market_data,
        broker=broker,
        repository=repository,
        config=config,
        user_id="cli",
    )

    print(f"Running backtest: {strategy.name} on {config.symbol}...")
    print(f"  Period: {bars[0].timestamp.date()} to {bars[-1].timestamp.date()}")
    print(f"  Capital: ${config.initial_capital:,.0f}")
    print(f"  Position size: {config.position_size}")
    print()

    result = runner.run()
    _print_results(result)


def _load_bars(config: BacktestConfig, db_path: Path) -> list[BarData]:
    """Load bars from SQLite data store, exit if empty."""
    data_store = SQLiteMarketDataStore(db_path)
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

    bars: list[BarData] = data_store.load_bars(config.symbol, config.granularity, start, end)
    data_store.close()
    if not bars:
        print(f"No data found for {config.symbol} ({config.granularity})")
        sys.exit(1)

    print(f"Loaded {len(bars)} bars for {config.symbol}")
    return bars


def _parse_params(param_args: list[str]) -> dict[str, int | float]:
    """Parse --param key=value arguments into a single-value dict."""
    params: dict[str, int | float] = {}
    for arg in param_args:
        if "=" not in arg:
            print(f"Invalid --param format: {arg!r} (expected key=value)")
            sys.exit(1)
        key, value_str = arg.split("=", 1)
        value_str = value_str.strip()
        try:
            params[key] = int(value_str)
        except ValueError:
            try:
                params[key] = float(value_str)
            except ValueError:
                print(f"Invalid --param value for {key!r}: {value_str!r}")
                sys.exit(1)
    return params


def _default_params(strategy_name: str) -> dict[str, int | float]:
    """Build a params dict from strategy metadata defaults."""
    meta = STRATEGY_METADATA[strategy_name]()
    return {p.key: p.default for p in meta.params}


def _parse_param_grid(param_args: list[str]) -> dict[str, list[int | float]]:
    """Parse --param key=v1,v2,v3 arguments into a grid dict."""
    grid: dict[str, list[int | float]] = {}
    for arg in param_args:
        if "=" not in arg:
            print(f"Invalid --param format: {arg!r} (expected key=v1,v2,...)")
            sys.exit(1)
        key, values_str = arg.split("=", 1)
        values: list[int | float] = []
        for v_str in values_str.split(","):
            v_str = v_str.strip()
            try:
                values.append(int(v_str))
            except ValueError:
                try:
                    values.append(float(v_str))
                except ValueError:
                    print(f"Invalid --param value for {key!r}: {v_str!r}")
                    sys.exit(1)
        grid[key] = values
    return grid


def _cmd_sweep(args: argparse.Namespace) -> None:
    """Execute the sweep command."""
    from aurex_trade.backtest.sweep import ParameterSweep

    strategy_name: str = args.strategy
    if strategy_name not in STRATEGY_REGISTRY:
        print(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    param_grid = _parse_param_grid(args.param)

    config = BacktestConfig(
        symbol=args.symbol,
        granularity=args.granularity,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size=args.position_size,
        spread_pips=args.spread,
        slippage_pips=args.slippage,
        commission_per_trade=args.commission,
        deterministic_seed=args.seed,
    )

    bars = _load_bars(config, Path(args.db_path))

    # Grid strategies manage their own risk — disable engine
    _sample = STRATEGY_REGISTRY[strategy_name]({k: vs[0] for k, vs in param_grid.items()})
    is_grid_strategy = hasattr(_sample, "report_fill")
    del _sample
    risk_engine = RiskEngine(
        max_position_size=args.max_position,
        max_daily_loss=args.max_daily_loss,
        max_trades_per_day=args.max_trades_per_day,
        require_stop_loss=not args.no_require_stop_loss,
        risk_per_trade=args.risk_per_trade,
        max_drawdown_pct=args.max_drawdown_pct,
        max_consecutive_losses=args.max_consecutive_losses,
        enabled=not is_grid_strategy,
    )

    sweep = ParameterSweep(
        strategy_factory=STRATEGY_REGISTRY[strategy_name],
        param_grid=param_grid,
        bars=bars,
        config=config,
        risk_engine=risk_engine,
        rank_by=args.rank_by,
        param_validator=PARAM_VALIDATORS.get(strategy_name),
        user_id="cli",
    )

    print(f"\nRunning parameter sweep: {strategy_name}")
    print(f"  Grid: {param_grid}")
    print()

    result = sweep.run()
    _print_sweep_results(result)


def _cmd_walk_forward(args: argparse.Namespace) -> None:
    """Execute the walk-forward command."""
    from aurex_trade.backtest.walk_forward import WalkForwardValidator

    strategy_name: str = args.strategy
    if strategy_name not in STRATEGY_REGISTRY:
        print(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    param_grid = _parse_param_grid(args.param)

    config = BacktestConfig(
        symbol=args.symbol,
        granularity=args.granularity,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size=args.position_size,
        spread_pips=args.spread,
        slippage_pips=args.slippage,
        commission_per_trade=args.commission,
        deterministic_seed=args.seed,
    )

    bars = _load_bars(config, Path(args.db_path))

    # Grid strategies manage their own risk — disable engine
    _sample = STRATEGY_REGISTRY[strategy_name]({k: vs[0] for k, vs in param_grid.items()})
    is_grid_strategy = hasattr(_sample, "report_fill")
    del _sample
    risk_engine = RiskEngine(
        max_position_size=args.max_position,
        max_daily_loss=args.max_daily_loss,
        max_trades_per_day=args.max_trades_per_day,
        require_stop_loss=not args.no_require_stop_loss,
        risk_per_trade=args.risk_per_trade,
        max_drawdown_pct=args.max_drawdown_pct,
        max_consecutive_losses=args.max_consecutive_losses,
        enabled=not is_grid_strategy,
    )

    validator = WalkForwardValidator(
        strategy_factory=STRATEGY_REGISTRY[strategy_name],
        param_grid=param_grid,
        bars=bars,
        config=config,
        risk_engine=risk_engine,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        rank_by=args.rank_by,
        param_validator=PARAM_VALIDATORS.get(strategy_name),
        user_id="cli",
    )

    window_size = args.train_bars + args.test_bars
    num_windows = len(bars) // window_size

    print(f"\nRunning walk-forward validation: {strategy_name}")
    print(f"  Grid: {param_grid}")
    print(f"  Windows: {num_windows} (train={args.train_bars}, test={args.test_bars})")
    print()

    result = validator.run()
    _print_walk_forward_results(result)


def _print_results(result: BacktestResult) -> None:
    """Print backtest results to stdout."""
    m = result.metrics
    print("=" * 60)
    print(f"  BACKTEST RESULTS - {result.strategy_name}")
    print("=" * 60)
    print(f"  Symbol:           {result.symbol}")
    if result.start_date and result.end_date:
        print(f"  Period:           {result.start_date.date()} to {result.end_date.date()}")
    print()
    print("  --- Performance ---")
    print(f"  Total P&L:        ${m.total_pnl:,.2f}")
    print(f"  Final Capital:    ${m.final_capital:,.2f}")
    print(f"  Return:           {((m.final_capital / m.initial_capital) - 1) * 100:.2f}%")
    print()
    print("  --- Trades ---")
    print(f"  Total Trades:     {m.trade_count}")
    print(f"  Win / Loss:       {m.win_count} / {m.loss_count}")
    print(f"  Win Rate:         {m.win_rate * 100:.1f}%")
    print(f"  Expectancy:       ${m.expectancy:,.2f} per trade")
    print(f"  Profit Factor:    {m.profit_factor:.2f}")
    print()
    print("  --- Risk ---")
    print(f"  Max Drawdown:     ${m.max_drawdown:,.2f} ({m.max_drawdown_pct * 100:.2f}%)")
    print(f"  Sharpe Ratio:     {m.sharpe_ratio:.2f}")
    print()
    print("  --- Costs ---")
    print(f"  Total Commission: ${m.total_commission:,.2f}")
    print("=" * 60)


def _print_sweep_results(result: SweepResult) -> None:
    """Print sweep results as a ranked table."""
    print("=" * 80)
    print(
        f"  PARAMETER SWEEP - {result.results[0].strategy_name if result.results else '?'}"
        f" ({result.total_combinations} combinations)"
    )
    print("=" * 80)
    print()

    # Header
    print(
        f"{'Rank':<5} {'Params':<25} {'P&L':>10} {'Win%':>7} {'Sharpe':>8} {'PF':>7} {'Trades':>7}"
    )
    print("-" * 69)

    for i, r in enumerate(result.results, 1):
        m = r.metrics
        params_str = " ".join(f"{k}={v}" for k, v in r.parameters.items())
        print(
            f"{i:<5} {params_str:<25} ${m.total_pnl:>8,.2f}"
            f" {m.win_rate * 100:>5.1f}%"
            f" {m.sharpe_ratio:>7.2f}"
            f" {m.profit_factor:>6.2f}"
            f" {m.trade_count:>6}"
        )

    print()
    print(f"  Ranked by: {result.rank_metric}")
    print("=" * 80)


def _print_walk_forward_results(result: WalkForwardResult) -> None:
    """Print walk-forward results with per-window and aggregate."""
    print("=" * 80)
    print(f"  WALK-FORWARD VALIDATION - {result.strategy_name} ({len(result.windows)} windows)")
    print("=" * 80)
    print()

    # Per-window table
    print(
        f"{'Window':<8} {'Best Params':<25} {'Train P&L':>11} {'Test P&L':>10} {'Test Sharpe':>12}"
    )
    print("-" * 66)

    for w in result.windows:
        params_str = " ".join(f"{k}={v}" for k, v in w.best_params.items())
        print(
            f"{w.window_index + 1:<8}"
            f" {params_str:<25}"
            f" ${w.train_result.metrics.total_pnl:>9,.2f}"
            f" ${w.test_result.metrics.total_pnl:>8,.2f}"
            f" {w.test_result.metrics.sharpe_ratio:>11.2f}"
        )

    # Aggregate
    agg = result.aggregate_test_metrics
    print()
    print("  --- Aggregate Out-of-Sample ---")
    print(f"  Total P&L:    ${agg.total_pnl:,.2f}")
    print(f"  Sharpe Ratio: {agg.sharpe_ratio:.2f}")
    print(f"  Win Rate:     {agg.win_rate * 100:.1f}%")
    print(f"  Trade Count:  {agg.trade_count}")
    print(f"  Max Drawdown: ${agg.max_drawdown:,.2f} ({agg.max_drawdown_pct * 100:.2f}%)")
    print("=" * 80)
