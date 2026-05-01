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
| `web/` | everything | (composition root — web server) |

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
│   ├── models.py       # Frozen dataclasses: BarData, Signal, Order, Trade, Position, RiskDecision, AccountState
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
│   ├── cli.py          # CLI subcommands: download-data, run, sweep, walk-forward
│   ├── config.py       # BacktestConfig (Pydantic Settings)
│   ├── runner.py       # BacktestRunner — core orchestration loop
│   ├── sweep.py        # ParameterSweep — grid search over strategy params
│   ├── walk_forward.py # WalkForwardValidator — train/test window validation
│   └── results.py      # BacktestResult, SweepResult, WalkForwardResult
├── engine/
│   └── trading_engine.py  # Main trading loop — depends ONLY on ports
├── web/
│   ├── __main__.py     # Entry point for `python -m aurex_trade.web`
│   ├── app.py          # FastAPI app factory (composition root)
│   ├── config.py       # WebConfig (Pydantic Settings)
│   ├── schemas.py      # Pydantic request/response models
│   ├── tasks.py        # Background task registry (ThreadPoolExecutor)
│   ├── dependencies.py # FastAPI Depends callables
│   ├── _run_helpers.py # Shared runner factories (backtest/sweep/walk-forward)
│   ├── routers/        # API route handlers (health, backtest, bot, settings, htmx)
│   ├── templates/      # Jinja2 + HTMX templates (DaisyUI via CDN)
│   │   ├── pages/      # Full page templates (backtest, sweep, walk_forward, bot, settings)
│   │   └── partials/   # HTMX fragments (loading, result, error for each task type)
│   └── static/         # CSS assets
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
just web        # Run web server (http://127.0.0.1:8000)
just web-dev    # Run web server with auto-reload
just sync       # Install/sync dependencies

# Backtesting
just download-data --symbol XAU_USD --granularity M1 --start 2025-04-14 --end 2025-04-18
just backtest --short-window 10 --long-window 30 --capital 100000 --spread 0.6 --slippage 0.2

# Parameter sweep (grid search)
just sweep --strategy sma_crossover --param short_window=5,10,15,20 --param long_window=20,30,50 --spread 0.6

# Walk-forward validation (train/test on unseen data)
just walk-forward --strategy sma_crossover --param short_window=5,10,20 --param long_window=20,30,50 --spread 0.6
```

## Risk Engine

The `RiskEngine` is the mandatory gate between strategy signals and order execution.
Every signal passes through `evaluate()` — no trade can bypass this check.

### Rules (evaluated in priority order)

| # | Rule | Config | Behavior |
|---|------|--------|----------|
| 1 | Kill switch | `RISK_KILL_SWITCH` | Rejects ALL signals immediately |
| 2 | Stop-loss enforcement | `RISK_REQUIRE_STOP_LOSS` | Rejects signals without a stop_loss price (configurable) |
| 3 | Max drawdown breaker | `RISK_MAX_DRAWDOWN_PCT` | Halts trading if equity drops >N% from peak |
| 4 | Consecutive loss pause | `RISK_MAX_CONSECUTIVE_LOSSES` | Halts trading after N losing trades in a row |
| 5 | Max position size | `RISK_MAX_POSITION_SIZE` | Rejects if position already at limit |
| 6 | Max daily loss | `RISK_MAX_DAILY_LOSS` | Rejects if daily P&L exceeds loss threshold |
| 7 | Trade frequency | `RISK_MAX_TRADES_PER_DAY` | Rejects if too many trades today |

### Position Sizing

Dynamic position sizing replaces fixed quantities:
```
units = (equity × risk_per_trade) / stop_distance
```
Capped at `max_position_size`. Falls back to configured `position_size` when
stop-loss is not available (i.e., `require_stop_loss=False`).

### Stop-Loss via ATR

The SMA Crossover strategy computes stop-loss using Average True Range:
- **LONG**: `stop_loss = entry_price - (atr_multiplier × ATR)`
- **SHORT**: `stop_loss = entry_price + (atr_multiplier × ATR)`
- Configurable via `STRATEGY_ATR_MULTIPLIER` (default 2.0) and `STRATEGY_ATR_PERIOD` (default 14)

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
just backtest --short-window 20 --long-window 50 --spread 0.6 --slippage 0.2
```

### Key Properties

- **Deterministic**: Same seed + same data = identical results every time
- **Strategy-agnostic**: Any class satisfying the `Strategy` Protocol works
- **Realistic costs**: Configurable spread, slippage (randomized per fill), commission
- **Full risk engine**: Same risk checks as live (stop-loss, drawdown, consecutive losses, position limits, daily loss, trade frequency)
- **Dynamic position sizing**: Risk-based sizing `units = (equity * risk_pct) / stop_distance`

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
| `--spread` | 1.5 | Spread in price units (OANDA XAU_USD actual: ~0.6) |
| `--slippage` | 0.5 | Max slippage in price units (realistic: ~0.2) |
| `--commission` | 0.0 | Commission per trade |
| `--seed` | 42 | Random seed (determinism) |
| `--max-position` | 10 | Risk: max position size |
| `--max-daily-loss` | 500.0 | Risk: daily loss limit |
| `--max-trades-per-day` | 100 | Risk: trade frequency limit |
| `--risk-per-trade` | 0.02 | Risk: fraction of equity per trade |
| `--max-drawdown-pct` | 0.20 | Risk: max drawdown from peak before halt |
| `--max-consecutive-losses` | 5 | Risk: pause after N consecutive losses |
| `--no-require-stop-loss` | (flag) | Disable stop-loss enforcement |

### Metrics Output

| Metric | What it means |
|--------|---------------|
| Total P&L | Net profit/loss after all trades |
| Win Rate | % of completed round trips that were profitable |
| Expectancy | Average $ per completed trade |
| Profit Factor | Gross profit / gross loss (>1 = profitable) |
| Max Drawdown | Largest peak-to-trough equity drop |
| Sharpe Ratio | Risk-adjusted return (annualized) |

### Parameter Sweep (Grid Search)

Automatically tests all parameter combinations and ranks by a metric:

```bash
just sweep --strategy sma_crossover \
    --param short_window=5,10,15,20 --param long_window=20,30,50,100 \
    --spread 0.6 --slippage 0.2 --rank-by sharpe_ratio
```

- Generic `--param key=v1,v2,...` design — works for any strategy
- Invalid combos filtered automatically (e.g. short >= long for SMA)
- Deterministic — same inputs always produce identical rankings
- Strategy registry in `backtest/cli.py` maps names to factory callables

### Walk-Forward Validation

Prevents overfitting by validating best params on unseen data:

```bash
just walk-forward --strategy sma_crossover \
    --param short_window=5,10,20 --param long_window=20,30,50 \
    --train-bars 7200 --test-bars 7200 --spread 0.6
```

- Non-overlapping windows: Train [Wk1] → Test [Wk2], Train [Wk3] → Test [Wk4], ...
- Default: 7200 bars train + 7200 bars test = 1 week each (M1)
- Configurable via `--train-bars` and `--test-bars` for different strategies
- Aggregates out-of-sample metrics across all test windows

### Data Storage

Historical bars are stored as CSV in `data/historical/{SYMBOL}_{GRANULARITY}.csv`.
Format: `timestamp,open,high,low,close,volume,symbol`. Re-downloading overwrites
the existing file for that symbol/granularity pair.

## Web Interface

The web layer (`src/aurex_trade/web/`) is a FastAPI composition root serving both
an API and HTMX-powered UI. It reuses the same domain, ports, and adapters as the
CLI — no domain changes needed.

### Architecture

- **Composition root**: `web/app.py` creates the FastAPI app, wires adapters
- **Background tasks**: `TaskRegistry` uses `ThreadPoolExecutor(max_workers=2)` for
  CPU-bound backtest/sweep/walk-forward jobs
- **API pattern**: POST submits job → returns `task_id` → GET polls status/result
- **HTMX pattern**: POST submits via `json-enc` → returns loading HTML with
  `hx-trigger="every 2s"` → polls until done → swaps in results fragment
- **Dual routers**: `/api/*` returns JSON (programmatic), `/htmx/*` returns HTML (UI)
- **Runner helpers**: `_run_helpers.py` shared by both routers (no duplication)
- **Templates**: Jinja2 + HTMX (polling via `hx-get` + `hx-trigger`)
- **Charts**: Chart.js via CDN — equity curves rendered in result partials
- **Styling**: DaisyUI + Tailwind via CDN (no bundler)

### API Endpoints (JSON)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Service health check |
| POST | `/api/backtest` | Submit backtest |
| GET | `/api/backtest/{task_id}` | Poll backtest result |
| POST | `/api/sweep` | Submit parameter sweep |
| GET | `/api/sweep/{task_id}` | Poll sweep result |
| POST | `/api/walk-forward` | Submit walk-forward validation |
| GET | `/api/walk-forward/{task_id}` | Poll walk-forward result |
| POST | `/api/bot/start` | Start trading bot (stub) |
| POST | `/api/bot/stop` | Stop trading bot (stub) |
| GET | `/api/bot/status` | Bot running status |
| GET | `/api/settings` | Current config (secrets redacted) |

### HTMX Endpoints (HTML fragments)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/htmx/backtest/submit` | Submit backtest, return loading partial |
| GET | `/htmx/backtest/{task_id}/poll` | Poll: returns loading/result/error HTML |
| POST | `/htmx/sweep/submit` | Submit sweep, return loading partial |
| GET | `/htmx/sweep/{task_id}/poll` | Poll: returns loading/result/error HTML |
| POST | `/htmx/walk-forward/submit` | Submit walk-forward, return loading partial |
| GET | `/htmx/walk-forward/{task_id}/poll` | Poll: returns loading/result/error HTML |

### Pages

| Path | Description |
|------|-------------|
| `/` | Dashboard |
| `/backtest` | Single backtest (form + results + equity curve chart) |
| `/sweep` | Parameter grid search (form + ranked results table) |
| `/walk-forward` | Train/test validation (form + per-window results) |
| `/bot` | Bot control (start/stop) |
| `/settings` | Current configuration |

### Configuration

Environment variables (prefix `WEB_`):
- `WEB_HOST` — bind address (default: `127.0.0.1`)
- `WEB_PORT` — port (default: `8000`)
- `WEB_RELOAD` — auto-reload on file changes (default: `false`)
- `WEB_LOG_LEVEL` — log level (default: `INFO`)

## How to Extend

### Adding a New Strategy

1. Create `src/aurex_trade/domain/strategy/your_strategy.py`
2. Implement the `Strategy` Protocol from `base.py`:
   - Must have `name: str` property
   - Must implement `generate(bars: list[BarData]) -> Signal | None`
3. Register it in `app.py` composition root
4. Add tests in `tests/unit/domain/test_your_strategy.py`
5. Add configuration params to `StrategyConfig` if needed
6. Register in `backtest/cli.py`:
   - Add factory to `STRATEGY_REGISTRY` dict
   - Add validator to `PARAM_VALIDATORS` dict (if params have constraints)
7. Sweep/validate: `just sweep --strategy your_strategy --param key=v1,v2`

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
