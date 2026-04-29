"""Backtest CLI — subcommands for downloading data and running backtests."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from aurex_trade.adapters.backtest.broker import SimulatedBrokerAdapter
from aurex_trade.adapters.backtest.data_store import HistoricalDataStore
from aurex_trade.adapters.backtest.market_data import HistoricalMarketDataAdapter
from aurex_trade.adapters.memory.repository import InMemoryRepository
from aurex_trade.adapters.oanda.connection import OANDAConnection
from aurex_trade.adapters.oanda.downloader import OANDAHistoricalDownloader
from aurex_trade.backtest.config import BacktestConfig
from aurex_trade.backtest.results import BacktestResult
from aurex_trade.backtest.runner import BacktestRunner
from aurex_trade.config import OANDAConfig
from aurex_trade.domain.risk.engine import RiskEngine
from aurex_trade.domain.strategy.sma_crossover import SMACrossover


def main() -> None:
    """Entry point for the backtest CLI."""
    parser = argparse.ArgumentParser(
        prog="aurex_trade.backtest",
        description="aurexTrade Backtesting Framework",
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
    dl_parser.add_argument(
        "--data-dir", default="data/historical", help="Output directory"
    )

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run a backtest")
    run_parser.add_argument("--symbol", default="XAU_USD", help="Instrument symbol")
    run_parser.add_argument("--granularity", default="M1", help="Bar granularity")
    run_parser.add_argument("--start", default="", help="Start date filter (YYYY-MM-DD)")
    run_parser.add_argument("--end", default="", help="End date filter (YYYY-MM-DD)")
    run_parser.add_argument(
        "--capital", type=float, default=100_000.0, help="Initial capital"
    )
    run_parser.add_argument(
        "--position-size", type=float, default=1.0, help="Units per trade"
    )
    run_parser.add_argument(
        "--short-window", type=int, default=10, help="SMA short window"
    )
    run_parser.add_argument(
        "--long-window", type=int, default=30, help="SMA long window"
    )
    run_parser.add_argument(
        "--spread", type=float, default=1.5, help="Spread in price units"
    )
    run_parser.add_argument(
        "--slippage", type=float, default=0.5, help="Slippage in price units"
    )
    run_parser.add_argument(
        "--commission", type=float, default=0.0, help="Commission per trade"
    )
    run_parser.add_argument("--seed", type=int, default=42, help="Random seed")
    run_parser.add_argument(
        "--data-dir", default="data/historical", help="Data directory"
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

    args = parser.parse_args()

    if args.command == "download-data":
        _cmd_download_data(args)
    elif args.command == "run":
        _cmd_run(args)


def _cmd_download_data(args: argparse.Namespace) -> None:
    """Execute the download-data command."""
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)

    oanda_config = OANDAConfig()
    connection = OANDAConnection(oanda_config)
    connection.connect()

    data_store = HistoricalDataStore(Path(args.data_dir))
    downloader = OANDAHistoricalDownloader(connection, data_store)

    try:
        count = downloader.download(args.symbol, args.granularity, start, end)
        print(f"Downloaded {count} candles for {args.symbol} ({args.granularity})")
        print(f"Saved to: {args.data_dir}/{args.symbol}_{args.granularity}.csv")
    finally:
        connection.disconnect()


def _cmd_run(args: argparse.Namespace) -> None:
    """Execute the run command."""
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
        data_dir=Path(args.data_dir),
        bar_count=args.long_window + 5,
    )

    # Load historical data
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

    bars = data_store.load_bars(config.symbol, config.granularity, start, end)
    if not bars:
        print(f"No data found for {config.symbol} ({config.granularity})")
        sys.exit(1)

    print(f"Loaded {len(bars)} bars for {config.symbol}")

    # Wire components
    strategy = SMACrossover(
        short_window=args.short_window, long_window=args.long_window
    )
    risk_engine = RiskEngine(
        max_position_size=args.max_position,
        max_daily_loss=args.max_daily_loss,
        max_trades_per_day=args.max_trades_per_day,
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

    # Run backtest
    runner = BacktestRunner(
        strategy=strategy,
        risk_engine=risk_engine,
        market_data=market_data,
        broker=broker,
        repository=repository,
        config=config,
    )

    print(f"Running backtest: {strategy.name} on {config.symbol}...")
    print(f"  Period: {bars[0].timestamp.date()} to {bars[-1].timestamp.date()}")
    print(f"  Capital: ${config.initial_capital:,.0f}")
    print(f"  Position size: {config.position_size}")
    print()

    result = runner.run()
    _print_results(result)


def _print_results(result: BacktestResult) -> None:
    """Print backtest results to stdout."""
    m = result.metrics
    print("=" * 60)
    print(f"  BACKTEST RESULTS — {result.strategy_name}")
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
