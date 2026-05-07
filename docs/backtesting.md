# aurexTrade — Backtesting

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

# 2. Run a backtest with SMA Crossover
just backtest --strategy sma_crossover --param short_window=10 --param long_window=30

# 3. Run with RSI Mean-Reversion
just backtest --strategy rsi_mean_reversion --param period=14 --param overbought=70 --param oversold=30

# 4. Adjust costs for realistic simulation
just backtest --strategy sma_crossover --param short_window=20 --param long_window=50 --spread 0.6 --slippage 0.2
```

## Key Properties

- **Deterministic**: Same seed + same data = identical results every time
- **Strategy-agnostic**: Any class satisfying the `Strategy` Protocol works
- **Realistic costs**: Configurable spread, slippage (randomized per fill), commission
- **Full risk engine**: Same risk checks as live (stop-loss, drawdown, consecutive losses, position limits, daily loss, trade frequency)
- **Dynamic position sizing**: Risk-based sizing `units = (equity * risk_pct) / stop_distance`

## CLI: `run` Subcommand

```bash
just backtest --strategy <name> [--param key=value ...] [options]
```

### Strategy Selection

| Flag | Default | Description |
|------|---------|-------------|
| `--strategy` | sma_crossover | Strategy to use (any registered name) |
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
# SMA Crossover sweep
just sweep --strategy sma_crossover \
    --param short_window=5,10,15,20 --param long_window=20,30,50,100 \
    --spread 0.6 --slippage 0.2 --rank-by sharpe_ratio

# RSI Mean-Reversion sweep
just sweep --strategy rsi_mean_reversion \
    --param period=7,14,21 --param overbought=70,75,80 --param oversold=20,25,30 \
    --spread 0.6
```

- Generic `--param key=v1,v2,...` design — works for any strategy
- Invalid combos filtered automatically (e.g. short >= long for SMA, oversold >= overbought for RSI)
- Deterministic — same inputs always produce identical rankings
- Strategy registry in `backtest/cli.py` maps names to factory callables

## Walk-Forward Validation

Prevents overfitting by validating best params on unseen data:

```bash
just walk-forward --strategy sma_crossover \
    --param short_window=5,10,20 --param long_window=20,30,50 \
    --train-bars 7200 --test-bars 7200 --spread 0.6

just walk-forward --strategy rsi_mean_reversion \
    --param period=7,14,21 --param overbought=70,75 --param oversold=25,30 \
    --train-bars 7200 --test-bars 7200 --spread 0.6
```

- Non-overlapping windows: Train [Wk1] -> Test [Wk2], Train [Wk3] -> Test [Wk4], ...
- Default: 7200 bars train + 7200 bars test = 1 week each (M1)
- Configurable via `--train-bars` and `--test-bars` for different strategies
- Aggregates out-of-sample metrics across all test windows

## Data Storage

Historical bars are stored as CSV in `data/historical/{SYMBOL}_{GRANULARITY}.csv`.
Format: `timestamp,open,high,low,close,volume,symbol`. Re-downloading overwrites
the existing file for that symbol/granularity pair.
