# CLAUDE.md — aurexTrade LLM Onboarding

This file is the primary reference for any LLM session working on aurexTrade.
Read this fully before making any changes.

## What is aurexTrade?

An automated gold trading bot that connects to OANDA for forex/CFD trading.
Single-user, single-process Python application. Currently targeting paper trading
with a path to live trading.

## Architecture: Hexagonal (Ports & Adapters)

```
Market Data → Strategy → Risk Engine → Execution → Broker → Persistence
```

### Layer Rules (STRICT — never violate)

| Layer | May import from | Must NOT import from |
|---|---|---|
| `domain/` | Python stdlib only | ports, adapters, engine, config |
| `ports/` | domain | adapters, engine |
| `adapters/` | domain, ports | engine, other adapters |
| `engine/` | domain, ports | adapters (injected via ports) |
| `app.py` | everything | (composition root — wires it all) |

**The domain NEVER depends on external libraries.** All external dependencies
are isolated in adapters.

### Module Map

```
src/aurex_trade/
├── app.py              # Composition root — wires adapters to ports, starts engine
├── config.py           # Pydantic Settings — AppConfig loaded from .env
├── metrics.py          # SHARED: PerformanceMetrics + calculate_metrics() (backtest + live)
├── domain/
│   ├── enums.py        # TradingMode, OrderSide, SignalType, OrderStatus, RiskAction
│   ├── models.py       # Frozen dataclasses: BarData, Signal, Order, Trade, Position, RiskDecision
│   ├── strategy/
│   │   ├── base.py     # Strategy Protocol
│   │   └── sma_crossover.py  # SMA Crossover implementation
│   └── risk/
│       └── engine.py   # RiskEngine — gates ALL trade decisions
├── ports/
│   ├── broker.py       # BrokerPort Protocol — order execution
│   ├── market_data.py  # MarketDataPort Protocol — price feeds
│   └── repository.py   # RepositoryPort Protocol — persistence
├── adapters/
│   ├── oanda/          # OANDA adapter (httpx → v20 REST API)
│   │   └── downloader.py  # Historical candle downloader (paginated)
│   ├── backtest/       # Backtesting adapters
│   │   ├── broker.py       # SimulatedBrokerAdapter (spread, slippage, commission)
│   │   ├── market_data.py  # HistoricalMarketDataAdapter (cursor-based replay)
│   │   └── data_store.py   # CSV read/write for historical bars
│   ├── memory/         # In-memory repository (local mode + tests)
│   ├── paper/          # Paper trading simulator
│   └── sqlite/         # SQLite persistence
├── backtest/
│   ├── __main__.py     # Entry point for `python -m aurex_trade.backtest`
│   ├── cli.py          # CLI subcommands: download-data, run
│   ├── config.py       # BacktestConfig (Pydantic Settings)
│   ├── runner.py       # BacktestRunner — core orchestration loop
│   └── results.py      # BacktestResult, BacktestTradeRecord
├── engine/
│   └── trading_engine.py  # Main trading loop — depends ONLY on ports
├── logging.py          # structlog configuration
└── __main__.py         # Entry point for `python -m aurex_trade`
```

## Conventions

### Naming
- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions/methods**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`
- **Protocols (ports)**: Suffix with `Port` (e.g., `BrokerPort`)
- **Adapters**: Prefix with provider + suffix with role (e.g., `OANDABrokerAdapter`)

### Patterns
- **Domain models**: Frozen dataclasses (`@dataclass(frozen=True)`)
- **Port interfaces**: Python `Protocol` classes (structural subtyping)
- **Dependency injection**: Constructor injection — adapters passed to engine at startup
- **Configuration**: Pydantic Settings with `.env` — validated on load
- **Logging**: structlog — structured, JSON to file, human-readable to console
- **Error handling**: Fail-closed — errors halt trading, never continue unsafely
- **IDs**: UUID4 for all entity identifiers
- **Timestamps**: Always UTC (`datetime.now(timezone.utc)`)

### Import Rules
- Domain modules import ONLY from `aurex_trade.domain` and Python stdlib
- Never use `from __future__ import annotations` in port/protocol files (breaks runtime Protocol checks)
- Adapters import their port Protocol + domain models, nothing else from `aurex_trade`

## Commands

```bash
just check      # Run lint + typecheck + test
just test       # Run pytest
just lint       # Run ruff check
just typecheck  # Run mypy (strict)
just fmt        # Format with ruff
just run        # Run bot (local mode)
just run-oanda-practice  # Run bot (OANDA practice mode)
just sync       # Install/sync dependencies

# Backtesting
just download-data --symbol XAU_USD --granularity M1 --start 2025-04-14 --end 2025-04-18
just backtest --short-window 10 --long-window 30 --capital 100000 --spread 1.5 --slippage 0.5
```

## Backtesting

The backtesting framework replays historical data through any `Strategy` Protocol
implementation, simulating fills with realistic spread, slippage, and commission.

### Architecture

The backtest reuses the same hexagonal boundaries as the live system:
- **Same**: Strategy Protocol, RiskEngine, domain models, InMemoryRepository
- **Different**: `HistoricalMarketDataAdapter` (replays bars from CSV),
  `SimulatedBrokerAdapter` (fills with spread/slippage), `BacktestRunner` (no sleep,
  finite iteration, equity tracking)

The runner (`backtest/runner.py`) depends only on ports and domain — never on
concrete adapters. The CLI (`backtest/cli.py`) is the composition root that wires
everything together.

### Workflow

```bash
# 1. Download historical data from OANDA
just download-data --symbol XAU_USD --granularity M1 --start 2025-04-14 --end 2025-04-18

# 2. Run a backtest (data loads from data/historical/XAU_USD_M1.csv)
just backtest --short-window 10 --long-window 30 --capital 100000

# 3. Try different parameters
just backtest --short-window 20 --long-window 50 --spread 1.5 --slippage 0.5
```

### Key Properties

- **Deterministic**: Same seed + same data = identical results every time
- **Strategy-agnostic**: Any class satisfying the `Strategy` Protocol works
- **Realistic costs**: Configurable spread, slippage (randomized per fill), commission
- **Full risk engine**: Same risk checks as live (position limits, daily loss, trade frequency)

### CLI Options (run subcommand)

| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | XAU_USD | Instrument |
| `--granularity` | M1 | Bar size |
| `--start` / `--end` | (all data) | Date filter (YYYY-MM-DD) |
| `--capital` | 100000 | Initial capital |
| `--position-size` | 1.0 | Units per trade |
| `--short-window` | 10 | SMA short period |
| `--long-window` | 30 | SMA long period |
| `--spread` | 1.5 | Spread in price units |
| `--slippage` | 0.5 | Max slippage in price units |
| `--commission` | 0.0 | Commission per trade |
| `--seed` | 42 | Random seed (determinism) |
| `--max-position` | 10 | Risk: max position size |
| `--max-daily-loss` | 500.0 | Risk: daily loss limit |
| `--max-trades-per-day` | 100 | Risk: trade frequency limit |

### Metrics Output

| Metric | What it means |
|--------|---------------|
| Total P&L | Net profit/loss after all trades |
| Win Rate | % of completed round trips that were profitable |
| Expectancy | Average $ per completed trade |
| Profit Factor | Gross profit / gross loss (>1 = profitable) |
| Max Drawdown | Largest peak-to-trough equity drop |
| Sharpe Ratio | Risk-adjusted return (annualized) |

### Data Storage

Historical bars are stored as CSV in `data/historical/{SYMBOL}_{GRANULARITY}.csv`.
Format: `timestamp,open,high,low,close,volume,symbol`. Re-downloading overwrites
the existing file for that symbol/granularity pair.

## How to Extend

### Adding a New Strategy

1. Create `src/aurex_trade/domain/strategy/your_strategy.py`
2. Implement the `Strategy` Protocol from `base.py`:
   - Must have `name: str` property
   - Must implement `generate(bars: list[BarData]) -> Signal | None`
3. Register it in `app.py` composition root
4. Add tests in `tests/unit/domain/test_your_strategy.py`
5. Add configuration params to `StrategyConfig` if needed
6. Backtest it: add a strategy option in `backtest/cli.py` and run with historical data

### Adding a New Broker Adapter

1. Create `src/aurex_trade/adapters/your_broker/`
2. Implement `BrokerPort` Protocol from `ports/broker.py`
3. Implement `MarketDataPort` Protocol from `ports/market_data.py`
4. Wire it in `app.py` based on a new `TradingMode` or config flag
5. Add tests in `tests/unit/adapters/`
6. **CRITICAL**: Never store credentials in code — use environment variables

### Adding a New Persistence Backend

1. Create `src/aurex_trade/adapters/your_backend/`
2. Implement `RepositoryPort` Protocol from `ports/repository.py`
3. Wire it in `app.py`
4. Add integration tests

## What NOT To Do

- **Never bypass the risk engine** — every order MUST pass through risk checks
- **Never hardcode credentials** — all secrets via environment variables
- **Never import adapters from domain** — this violates hexagonal boundaries
- **Never use `eval()`, `exec()`, or `os.system()`** — command injection risk
- **Never use string concatenation in SQL** — always parameterized queries
- **Never log credentials, account numbers, or PII**
- **Never enable live trading without the double-gate** (`TRADING_MODE=live` + `LIVE_TRADING_CONFIRMED=true`)
- **Never use `Any` types** — mypy strict mode is enforced
- **Never commit `.env`** — it's gitignored for a reason

## MANDATORY: Pre-Commit Security Review

**This is NON-NEGOTIABLE.** Before EVERY commit, verify ALL of the following:

1. **No secrets in code** — no API keys, passwords, tokens, or credentials hardcoded or in committed files. Check all new/modified files.
2. **No SQL injection** — all database queries use parameterized statements (`?` placeholders), NEVER string concatenation or f-strings in SQL.
3. **No command injection** — no `os.system()`, `subprocess` with `shell=True`, or `eval()`/`exec()`.
4. **Input validation** — all external inputs (config, market data, broker responses) are validated before use. Malformed data must not crash the system or corrupt state.
5. **Financial safety** — risk engine cannot be bypassed; kill switch is always reachable; live trading requires double-gate confirmation.
6. **No sensitive data in logs** — credentials, account numbers, or PII are NEVER logged. Verify all log statements in changed code.
7. **Dependency audit** — if adding a new dependency, verify it is well-maintained and has no known CVEs. Prefer stdlib when possible.
8. **Fail-closed error handling** — errors must halt trading or skip the current cycle, NEVER continue with potentially corrupted state. No bare `except:` clauses.
9. **Type safety** — no `Any` types that could mask unsafe operations. All function signatures fully typed.
10. **Import boundary integrity** — domain never imports adapters; hexagonal boundary intact. Run `just check` to verify.

**If any check fails, fix it before committing. No exceptions.**

## Testing

- **Domain tests**: Pure unit tests, no mocks — deterministic inputs → expected outputs
- **Adapter tests**: Unit tests with mocked external services
- **Integration tests**: Marked with `@pytest.mark.integration` — use real (temp) resources
- Run `just test` before every commit
- Run `just test -m "not integration"` for fast feedback
