# AurexTrade — Backtesting

## Architecture

The backtesting framework replays historical data through any `Strategy` Protocol
implementation, simulating fills with realistic spread, slippage, and commission.

It reuses the same hexagonal boundaries as the live system:
- **Same**: Strategy Protocol, RiskEngine, domain models, InMemoryRepository
- **Different**: `HistoricalMarketDataAdapter` (replays bars from CSV),
  `SimulatedBrokerAdapter` (fills with spread/slippage), `BacktestRunner` (no sleep,
  finite iteration, equity tracking)

The runner (`backtest/runner.py`) depends only on ports and domain — never on
concrete adapters. The CLI (`backtest/cli.py`) is the composition root that wires
everything together.

## Workflow

```bash
# 1. Download historical data from OANDA
just download-data --symbol XAU_USD --granularity M1 --start 2025-04-14 --end 2025-04-18

# 2. Run a backtest with Ciby Sliding Grid (the primary strategy)
just backtest --strategy ciby_sliding_grid --param grid_spacing=10 --param anchor_gap=15

# 3. Adjust costs for realistic simulation
just backtest --strategy ciby_sliding_grid --param grid_spacing=10 --param stop_buffer=3 --spread 0.6 --slippage 0.2
```

```bash
# 4. Run with Ciby Hedged Doubling Grid (grid strategy — risk engine auto-disabled)
just backtest --strategy ciby_hedged_doubling_grid \
    --param spacing=20 --param units=2 \
    --param trailing_stop_distance=20 --param session_loss_limit=100 \
    --param whipsaw_limit=3 --spread 0.5
```

## Key Properties

- **Deterministic**: Same seed + same data = identical results every time
- **Strategy-agnostic**: Any class satisfying the `Strategy` Protocol works
- **Realistic costs**: Configurable spread, slippage (randomized per fill), commission
- **Full risk engine**: Same risk checks as live (stop-loss, drawdown, consecutive losses, position limits, daily loss, trade frequency)
- **Grid mode**: Auto-detected for strategies with `report_fill` — limit order simulation, stop-loss enforcement, signal drain loop, risk engine disabled (strategy manages own risk)
- **Dynamic position sizing**: Risk-based sizing `units = (equity * risk_pct) / stop_distance`

## CLI: `run` Subcommand

```bash
just backtest --strategy <name> [--param key=value ...] [options]
```

### Strategy Selection

| Flag | Default | Description |
|------|---------|-------------|
| `--strategy` | ciby_sliding_grid | Strategy to use (any registered name) |
| `--param` | (defaults) | Strategy parameter (repeatable, format: key=value) |

If no `--param` flags are provided, strategy metadata defaults are used.

### Market & Cost Options

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | XAU_USD | Instrument |
| `--granularity` | M1 | Bar size |
| `--start` / `--end` | (all data) | Date filter (YYYY-MM-DD) |
| `--capital` | 100000 | Initial capital |
| `--position-size` | 1.0 | Units per trade |
| `--spread` | 1.5 | Spread in price units |
| `--slippage` | 0.5 | Max slippage in price units |
| `--commission` | 0.0 | Commission per trade |
| `--seed` | 42 | Random seed (determinism) |

### Risk Options

| Flag | Default | Description |
|------|---------|-------------|
| `--max-position` | 10 | Max position size |
| `--max-daily-loss` | 500.0 | Daily loss limit |
| `--max-trades-per-day` | 100 | Trade frequency limit |
| `--risk-per-trade` | 0.02 | Fraction of equity per trade |
| `--max-drawdown-pct` | 0.20 | Max drawdown from peak before halt |
| `--max-consecutive-losses` | 5 | Pause after N consecutive losses |
| `--no-require-stop-loss` | (flag) | Disable stop-loss enforcement |

## Metrics Output

| Metric | What it means |
|--------|---------------|
| Total P&L | Net profit/loss after all trades |
| Win Rate | % of completed round trips that were profitable |
| Expectancy | Average $ per completed trade |
| Profit Factor | Gross profit / gross loss (>1 = profitable) |
| Max Drawdown | Largest peak-to-trough equity drop |
| Sharpe Ratio | Risk-adjusted return (annualized) |

## Parameter Sweep (Grid Search)

Automatically tests all parameter combinations and ranks by a metric:

```bash
# Ciby Sliding Grid sweep
just sweep --strategy ciby_sliding_grid \
    --param grid_spacing=5,10,20 --param anchor_gap=10,15 \
    --param stop_buffer=1,3 --spread 0.6 --slippage 0.2 --rank-by sharpe_ratio

# Ciby Hedged Doubling Grid sweep
just sweep --strategy ciby_hedged_doubling_grid \
    --param spacing=10,20 --param units=2,4 \
    --param trailing_stop_distance=10,20 --param session_loss_limit=100 \
    --param whipsaw_limit=3 --spread 0.5
```

- Generic `--param key=v1,v2,...` design — works for any strategy
- Invalid combos filtered automatically by each strategy's `PARAM_VALIDATORS` entry
- Deterministic — same inputs always produce identical rankings
- Strategy registry in `backtest/cli.py` maps names to factory callables

## Walk-Forward Validation

Prevents overfitting by validating best params on unseen data:

```bash
just walk-forward --strategy ciby_sliding_grid \
    --param grid_spacing=5,10 --param anchor_gap=10,15 --param stop_buffer=1,3 \
    --train-bars 7200 --test-bars 7200 --spread 0.6

# Ciby Hedged Doubling Grid walk-forward
just walk-forward --strategy ciby_hedged_doubling_grid \
    --param spacing=10,20 --param units=2,4 \
    --param trailing_stop_distance=10,20 --param session_loss_limit=100 \
    --param whipsaw_limit=3 --spread 0.5
```

- Non-overlapping windows: Train [Wk1] -> Test [Wk2], Train [Wk3] -> Test [Wk4], ...
- Default: 7200 bars train + 7200 bars test = 1 week each (M1)
- Configurable via `--train-bars` and `--test-bars` for different strategies
- Aggregates out-of-sample metrics across all test windows

## Web UI

The same backtest, sweep, and walk-forward functionality is available through the
web interface. Start with `just web` and visit `http://127.0.0.1:8000`.

- **Strategy selection**: Dropdown on each page shows all registered strategies
- **Dynamic parameters**: Form fields update automatically based on the selected
  strategy's metadata (labels, tooltips, defaults, and valid ranges)
- **API endpoint**: `GET /api/strategies` returns all registered strategies with
  their parameter metadata (useful for programmatic access)

The web UI supports all the same operations as the CLI — select a strategy,
configure parameters, and run backtests, sweeps, or walk-forward validation.

## Data Storage

Historical bars are stored in a shared SQLite database (`data/aurex_trade.db`)
in the `bars` table, keyed by `(symbol, granularity, timestamp)`.

Key properties:
- **Concurrent-safe**: WAL mode + `INSERT OR IGNORE` — multiple users can download
  overlapping ranges simultaneously without data loss or corruption.
- **Gap-only downloads**: Before fetching from OANDA, the CLI/web checks existing
  coverage (`MIN/MAX timestamp`). Only missing ranges at the start or end are
  downloaded. Overlapping inserts are harmless.
- **Shared across users**: The first user to download a range pays the cost;
  subsequent users get instant access to the same data.
- **Per-user preferences**: The `user_data_preferences` table remembers each
  user's last-used date range per symbol/granularity, pre-filling the UI on
  next visit.
