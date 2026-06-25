# CLAUDE.md — AurexTrade LLM Onboarding

Read this fully before making any changes.

## What is AurexTrade?

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
are isolated in adapters. See `docs/architecture.md` for full details.

### Module Map

```
src/aurex_trade/
├── app.py              # Composition root
├── config.py           # Pydantic Settings — AppConfig loaded from .env
├── metrics.py          # SHARED: PerformanceMetrics + calculate_metrics()
├── domain/
│   ├── enums.py        # TradingMode, OrderSide, SignalType, OrderStatus, RiskAction
│   ├── models.py       # Frozen dataclasses: BarData, Signal, Order, Trade, Position
│   ├── strategy/
│   │   ├── base.py         # Strategy Protocol + StrategyMetadata
│   │   ├── ciby_sliding_grid.py        # Ciby Sliding Grid (primary, live)
│   │   └── ciby_hedged_doubling_grid.py  # Ciby Hedged Doubling Grid (experimental)
│   └── risk/
│       └── engine.py   # RiskEngine — gates ALL trade decisions
├── ports/              # Protocol interfaces (BrokerPort, MarketDataPort, RepositoryPort, HistoricalDataPort, CredentialStorePort)
├── adapters/
│   ├── oanda/          # OANDA adapter (httpx → v20 REST API)
│   ├── backtest/       # Backtesting adapters (simulated broker, historical replay)
│   ├── memory/         # In-memory repository (local mode + tests)
│   ├── paper/          # Paper trading simulator
│   └── sqlite/         # SQLite persistence (repository, sessions, market data, user prefs, encrypted credentials)
├── backtest/
│   ├── cli.py          # CLI: download-data, run, sweep, walk-forward
│   ├── runner.py       # BacktestRunner — core orchestration loop
│   ├── sweep.py        # ParameterSweep — grid search
│   └── walk_forward.py # WalkForwardValidator — train/test validation
├── engine/
│   └── trading_engine.py  # Main trading loop
├── web/                # FastAPI app (API + HTMX UI, GET /api/strategies for metadata)
│   └── routers/        # Feature-based modules (see Router Organization below)
└── logging.py          # structlog configuration
```

### Web Router Organization

Routers follow a feature-based module pattern with transport separation:

```
web/routers/
├── __init__.py          # Package docstring only
├── health.py            # Simple single-endpoint routers stay flat
├── broker/
│   ├── __init__.py      # Exports combined `router` (includes api + htmx)
│   ├── _common.py       # Shared constants and validation (ALLOWED_SERVERS, validate_broker)
│   ├── api.py           # JSON endpoints: prefix /api/broker
│   └── htmx.py          # HTML fragment endpoints: prefix /htmx/broker
├── backtest/
│   ├── __init__.py      # Exports combined `router`
│   ├── api.py           # JSON: /api/backtest, /api/sweep, /api/walk-forward, etc.
│   └── htmx.py          # HTML: /htmx/backtest/submit, /htmx/sweep/submit, etc.
├── bot/
│   ├── __init__.py
│   └── api.py           # JSON: /api/bot/...
├── settings/
│   ├── __init__.py
│   └── api.py           # JSON: /api/settings
└── user_defaults/
    ├── __init__.py
    └── api.py           # JSON: /api/user-defaults/...
```

**Rules:**
- Each feature folder exports one `router` from `__init__.py`
- `api.py` = JSON in, JSON out. Pydantic request/response models. No template rendering.
- `htmx.py` = form data in, HTML fragments out. Template rendering. No JSON responses.
- No content-type sniffing — transport is determined by the endpoint, not the request headers
- `app.py` includes one router per feature (the combined one from `__init__.py`)
- Simple features with only one transport may stay as flat files (e.g., `health.py`)
- Each feature folder is self-contained — no cross-feature imports between routers

### User Model (CRITICAL)

| Concern | CLI (`app.py`, `backtest/cli.py`) | Web (`web/`) |
|---------|-----------------------------------|--------------|
| **User model** | Single-user (operator) | Multi-user (authenticated via OAuth) |
| **Credential source** | `.env` / environment variables | Per-user credential store (encrypted in DB) |
| **Isolation** | None needed — one operator | Strict — user A must never access user B's data |
| **Auth** | None (trusted local context) | Google OAuth + session cookies |

**Any code in the web layer that touches credentials, data access, or configuration
MUST be user-scoped.** The web app is designed from the ground up for multi-user
isolation. The CLI is a single-operator tool where shared environment config is
appropriate. Web flows NEVER fall back to shared `.env` credentials.

## Conventions

- **Files**: `snake_case.py` | **Classes**: `PascalCase` | **Constants**: `UPPER_SNAKE_CASE`
- **Protocols (ports)**: Suffix with `Port` | **Adapters**: Provider prefix + role suffix
- **Domain models**: Frozen dataclasses | **Port interfaces**: Python `Protocol`
- **DI**: Constructor injection | **Config**: Pydantic Settings + `.env`
- **Error handling**: Fail-closed — errors halt trading, never continue unsafely
- **IDs**: UUID4 | **Timestamps**: Always UTC
- Domain imports ONLY from `aurex_trade.domain` and Python stdlib
- Never use `from __future__ import annotations` in port/protocol files

## Commands

```bash
just check      # Run lint + typecheck + test
just test       # Run pytest
just lint       # Run ruff check
just typecheck  # Run mypy (strict)
just fmt        # Format with ruff
just run        # Run bot (local mode)
just web        # Run web server (http://127.0.0.1:8000)
just sync       # Install/sync dependencies

# Deployment — production: https://aurex.manikolbe.com
just deploy-local        # Build and start Docker containers locally
just deploy-local-down   # Stop local containers
just deploy-local-logs   # View local container logs
just deploy-prod         # Deploy to production VPS (push to main first)

# Production monitoring (SSH alias: aurex, app in ~/aurex-trade)
ssh aurex 'docker compose -f ~/aurex-trade/docker-compose.yml logs --tail=50 app'

# Useful grep filters for monitoring:
# Every engine log line carries bound context: user_id, run_id, strategy, session_seq.
# Bot config (strategy + risk params logged at startup; re-emitted on session_summary):
#   | grep "engine_started"
# Grid levels (anchor + all levels, logged once after first bar):
#   | grep "grid_initialized"
# Isolate one run (everything that run logged):
#   | grep '"run_id": "<run_id>"'
# Isolate one grid lifecycle within a run (anchor → close-all → re-anchor):
#   | grep '"session_seq": <n>'
# All trade activity (signals, fills, closures):
#   | grep "info" | grep -E "signal|trade_executed|trade_closed|position_updated|cycle_error"
# Just closure detection:
#   | grep "trade_closed_by_broker"
# Errors only:
#   | grep -E "error|exception|warning"
# Follow live (stream):
ssh aurex 'docker compose -f ~/aurex-trade/docker-compose.yml logs -f --tail=10 app'

# Backtesting (see docs/backtesting.md for full details)
just download-data --symbol XAU_USD --granularity M1 --start 2025-04-14 --end 2025-04-18
just backtest --strategy ciby_sliding_grid --param grid_spacing=10 --param anchor_gap=15
just backtest --strategy ciby_hedged_doubling_grid --param spacing=20 --param units=2
just sweep --strategy ciby_sliding_grid --param grid_spacing=5,10,20 --param anchor_gap=10,15 --spread 0.6
just walk-forward --strategy ciby_sliding_grid --param grid_spacing=5,10 --param stop_buffer=1,3
```

## Bot Configuration (Web UI)

The bot is configured entirely through the web UI at https://aurex.manikolbe.com.
Configuration is per-user (stored in SQLite via user preferences).

### Key Settings

| Setting | Description | Test Value | Notes |
|---------|-------------|------------|-------|
| **Strategy** | Trading strategy to use | `ciby_sliding_grid` | The primary (effectively only) strategy in real use |
| **Symbol** | Instrument to trade | `XAU_USD` | Gold vs USD |
| **Interval** | Seconds between cycles | `60` | 1 min for testing, 300+ for production |
| **Granularity** | OANDA candle granularity | `M1` | Must align with interval |

> Only two strategies are registered: `ciby_sliding_grid` (primary, live) and
> `ciby_hedged_doubling_grid` (experimental). The previously-registered
> `ciby_hedged_grid`, `simple_grid`, `sma_crossover`, and `rsi_mean_reversion`
> were removed as stale.

### Ciby Sliding Grid Strategy Parameters

The web UI renders these dynamically from `StrategyMetadata`; defaults below come from
`CibySlidingGridStrategy.metadata()`.

| Parameter | Description | Default |
|-----------|-------------|---------|
| `grid_spacing` | Distance between consecutive grid levels beyond the first ($) | `10` |
| `anchor_gap` | Distance from anchor to the first level above/below ($) | `15` |
| `buy_sell_offset` | Gap between buy and sell of a hedged pair, to clear the spread ($) | `0.9` |
| `anchor_units` | Units per side of the hedged pair at the anchor level | `10` |
| `grid_units` | Units per side of the hedged pair at non-anchor levels | `20` |
| `stop_buffer` | Extra distance past the next level where the stop sits ($) | `1.0` |
| `max_levels_ahead` | Max active levels kept on the trending side | `2` |
| `max_levels_behind` | Max active levels kept on the trailing side | `1` |
| `session_profit_target` | Close all & restart when session P&L hits this ($) | `100` |
| `session_loss_limit` | Close all & restart when session P&L drops below this ($) | `50` |
| `daily_loss_limit` | Stop trading for the day when cumulative P&L drops below this ($) | `200` |

### Risk Engine Settings (environment variables)

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_POSITION_SIZE` | Max units per position | `10` |
| `RISK_PER_TRADE` | Fraction of equity risked per trade | `0.02` |
| `MAX_DRAWDOWN_PCT` | Kill switch threshold (%) | `0.10` |
| `MAX_DAILY_LOSS` | Daily loss limit ($) | `500` |

### Starting the Bot for Testing

1. Navigate to web UI → Bot page
2. Select strategy: `ciby_sliding_grid`
3. Set params: `grid_spacing=10`, `anchor_gap=15`, `anchor_units=10`, `grid_units=20`, `stop_buffer=1`
4. Set interval: `60` (1 minute cycles for fast feedback)
5. Click Start → bot begins trading on connected OANDA account
6. Monitor via logs: `ssh aurex 'docker compose -f ~/aurex-trade/docker-compose.yml logs -f --tail=10 app'`

### Clean State for Testing

The bot does NOT automatically reconcile pre-existing positions on startup.
If testing closure detection, ensure a clean slate:
- Close all open XAU_USD trades in OANDA before starting
- Or: let the bot start fresh — it will track only trades it opens

## Analysing Bot Runs in Production

After deploying and letting the bot run, analyse its performance from the
**structured JSON logs**. A durable per-run summary also lives in the DB (`bot_runs`
table) — see below. **Full how-to: `docs/log-analysis.md`.**

**Why logs, not the DB, for detail:** The engine only persists MARKET orders via
`save_trade`. Limit/stop fills and — critically — **individual trade closures with
realized P&L are never written to SQLite** (the `trades` table has no realized_pnl
column). The structlog JSON log (`/app/logs/aurex_trade.log*`, rotated 10MB × 10
files) is the complete, event-sourced record. The `bot_runs` table is a per-run
*summary* (config + outcome + net P&L) that survives rotation, but it is not the
event log. The live equity/session charts in the web UI are in-memory and lost on
every redeploy/restart.

**Realized P&L = account-balance delta (not per-trade history).** OANDA's
transaction/trade *history* endpoints (`/transactions*`, `/trades/{id}`,
`/trades?state=CLOSED`) return **HTTP 504** once an account accumulates a large
history (~10k+ transactions), so the engine does **not** look up per-trade realized
P&L. Instead it snapshots the account **balance** (`/summary`, always fast — balance
changes only when P&L is realized) at run start, each session start (after a
close-all + re-anchor), and the UTC day boundary, and derives realized P&L from the
deltas. `balance` is logged on `engine_started`, `session_summary`,
`close_all_executed`, and `engine_stopped`; `session_realized` (per-session delta)
rides on `close_all_executed`. `bot_runs.net_realized_pnl` is the run's balance delta
(`final_balance − initial_balance`).

Per-trade realized P&L (for win-rate + the risk engine's consecutive-loss gate) is
computed **locally**, not from the history API: each open trade's entry price + stop
price are recorded at fill, and on a stop-out `trade_closed_by_broker` reports
`realized_pnl ≈ (stop − entry) × qty` (`realized_pnl_basis: stop_price`). Margin-trim
closes (`level_trimmed`) and close-all trades carry the **exact** P&L from the close
response. These per-trade values are accurate-but-secondary; the session/daily/run
totals always come from the balance delta. **Fail-closed:** if `/summary` (balance)
can't be read, the cycle is skipped and logged; ≥3 consecutive failures halt the bot
rather than trade on stale P&L.

Every engine log line carries bound context: **`user_id`, `run_id`, `strategy`,
`session_seq`** (a run = one `engine_started`→`engine_stopped`; a session = one grid
lifecycle: anchor → close-all → re-anchor).

### Workflow

```bash
just pull-logs      # docker-cp prod logs from aurex-app:/app/logs → logs/prod/ (gitignored)
just analyse        # analyse the latest run (summary + per-session P&L + anomalies)
just analyse --list                  # quick nutshell list of all runs (run_id, net P&L)
just analyse --list --json           # same, machine-readable
just analyse --run 2                 # analyse run #2 (by index)
just analyse --run aaaa1111          # …or by run_id prefix
just analyse --run 2 --timeline      # + price-annotated event playback
```

`scripts/analyse_run.py` reads the pulled logs and:
- **Filters to one user** by `user_id` (prod is multi-user). Reads all rotated
  `.log`/`.log.N` files.
- **Groups lines into runs by `run_id`.** Lines without a `run_id`
  (pre-instrumentation logs) are skipped, with a reported count. Config is taken from
  `engine_started`, falling back to the latest `session_summary` (which re-emits
  config) when the start line has rotated out.
- Reports **net realized P&L** (from the account-balance delta — authoritative;
  falls back to summed per-session deltas, then to summed per-closure P&L for
  pre-fix runs that logged it), **win rate, a per-session P&L breakdown, close-reason
  breakdown, errors, largest losses**, and a **playback timeline** where each event
  is annotated with the prevailing market price (`bars_fetched.latest_close` carried
  forward) and its `session_seq` — so you can replay exactly what happened against
  price and grid state.

### Identity & PII (PUBLIC REPO — STRICT)

This repo is public. The analysis tooling carries **no identifiers**:
- User identity (`user_id`, email) is read at runtime from `analysis.local.json`
  (gitignored via `analysis.local.*`) or a `--user-id` flag. Never hardcode it.
- Pulled logs land in `logs/prod/` (gitignored — they contain emails, OANDA
  account ids, OAuth user ids).
- Before committing, scan for leaks:
  `git grep -nI "<your-email>|<account-id>|<oauth-user-id>"`.

### Key log events the analyser reads

Every engine log line also carries the bound context fields `user_id`, `run_id`,
`strategy`, and `session_seq` (added once via structlog contextvars, merged onto
every line). The table below lists the event-specific payload.

| Event | Carries |
|-------|---------|
| `engine_started` | `run_id`, strategy, `strategy_params`, `risk_params`, `initial_equity`, `initial_balance` |
| `engine_stopped` | `total_cycles` (absence ⇒ run still active), `balance`, `run_realized` |
| `grid_initialized` | `session_seq`, `anchor_price` + full `levels` ladder |
| `bars_fetched` | `latest_close` (market price per cycle) |
| `close_all_executed` | `reason`, `trades_closed`, `balance`, `session_realized` (per-session realized P&L = balance delta — **authoritative**) |
| `trade_closed_by_broker` | `close_reason` (close_sl), `close_price`, `realized_pnl` (per-trade, computed locally from entry vs stop price), `realized_pnl_basis` (`stop_price`\|`last_price`\|`unknown`). Feeds win-rate + the risk engine's consecutive-loss gate; the **authoritative** session/run total still comes from the balance delta |
| `level_trimmed` | margin-trim close: `realized_pnl` (exact, from the close response), `broker_trade_id` |
| `balance_read_failed` / `balance_read_halt` | a `/summary` read failed (transient → skip cycle; ≥3 consecutive → halt, fail-closed) |
| `session_summary` | `cycles`, `equity`, `peak_equity`, `balance`, `run_realized`, `trades`; also re-emits `strategy_params`, `risk_params`, `symbol`, `interval` (config survives rotation) |
| `order_execution_failed`, `fast_poll_error` | failures/errors to flag |

### Durable run history (DB)

`bot_runs` (SQLite, user-scoped) holds one summary row per run — config, runtime,
status, and net P&L — written on `engine_started` (`status='running'`) and finalized
on `engine_stopped` (`status='stopped'`). A row stuck at `'running'` with no recent
log indicates a crashed run. It is a rollup, not an event log: the analyser remains
authoritative; the two should agree on net P&L for a given `run_id`. True independent
validation (vs. OANDA's transaction history) is future work.

## Risk Engine

The `RiskEngine` is the mandatory gate between strategy signals and order execution.
Every signal passes through `evaluate()` — no trade can bypass this check.
Rules: kill switch, stop-loss enforcement, max drawdown, consecutive losses,
max position size, daily loss limit, trade frequency. Position sizing is dynamic:
`units = (equity * risk_per_trade) / stop_distance`.

## How to Extend

### Adding a New Strategy

See `docs/strategies.md` for the full guide. Summary:

1. Create `src/aurex_trade/domain/strategy/your_strategy.py`
2. Implement the `Strategy` Protocol from `base.py`:
   - `name: str` property
   - `min_bars: int` property (minimum bars needed for signal generation)
   - `generate(bars: list[BarData]) -> Signal | None`
   - `metadata() -> StrategyMetadata` classmethod (with `ParamMeta` for each param)
3. Register in `backtest/cli.py`: add to `STRATEGY_REGISTRY`, `PARAM_VALIDATORS`, `STRATEGY_METADATA`
4. Add tests in `tests/unit/domain/test_your_strategy.py`
5. Verify: `just backtest --strategy your_strategy --param key=value`
6. **Web UI**: No template changes needed — the UI renders dynamically from strategy
   metadata. New strategies automatically appear in dropdowns with correct param
   fields, tooltips, and educational descriptions.

### Adding a New Broker/Persistence Adapter

1. Create adapter in `src/aurex_trade/adapters/your_provider/`
2. Implement the relevant port Protocol
3. Wire in `app.py` composition root
4. **CRITICAL**: Never store credentials in code — use environment variables

## What NOT To Do

- **Never bypass the risk engine** — every order MUST pass through risk checks
- **Never hardcode credentials** — all secrets via environment variables
- **Never import adapters from domain** — violates hexagonal boundaries
- **Never use `eval()`, `exec()`, or `os.system()`** — command injection risk
- **Never use string concatenation in SQL** — always parameterized queries
- **Never log credentials, account numbers, or PII**
- **Never enable live trading without the double-gate** (`TRADING_MODE=live` + `LIVE_TRADING_CONFIRMED=true`)
- **Never use `Any` types** — mypy strict mode is enforced

## MANDATORY: Pre-Commit Security Review

Before EVERY commit, verify:

1. **No secrets in code** — no API keys, passwords, tokens hardcoded
2. **No SQL injection** — parameterized queries only
3. **No command injection** — no `os.system()`, `shell=True`, `eval()`
4. **Input validation** — external inputs validated before use
5. **Financial safety** — risk engine not bypassed; kill switch reachable
6. **No sensitive data in logs** — credentials, PII never logged
7. **Dependency audit** — new deps are well-maintained, no CVEs
8. **Fail-closed** — errors halt trading, never continue unsafely
9. **Type safety** — no `Any` types; fully typed signatures
10. **Import boundary integrity** — `just check` passes

## Testing

- **Domain tests**: Pure unit tests, no mocks — deterministic inputs → expected outputs
- **Adapter tests**: Unit tests with mocked external services
- **Integration tests**: Marked with `@pytest.mark.integration`
- Run `just check` before every commit

## Documentation Strategy

### Tooling & Structure

- **User-facing docs**: MkDocs + Material theme, served at `/guide/` from the web app
- **Internal/dev docs**: Raw markdown in `docs/` (architecture, backtesting, strategies dev guide, user-guide)
- **Source**: `docs/user/` → published via MkDocs; `docs/*.md` → repo-only (never published)
- **Build**: `just docs` (build) / `just docs-serve` (local preview)

### Writing Guide (Diátaxis Framework)

All user-facing documentation follows the Diátaxis framework as a writing lens:

| Type | Purpose | Example |
|------|---------|---------|
| **Tutorial** | Learning-oriented, step-by-step | "Getting Started" |
| **Explanation** | Understanding-oriented, concepts | "Trading Concepts" |
| **How-To** | Task-oriented, problem-solving | (added as project grows) |
| **Reference** | Information-oriented, dry facts | "Glossary" |

### Audience Split (STRICT — never cross-contaminate)

| Location | Audience | Depth | Purpose |
|----------|----------|-------|---------|
| In-app tooltips/explainers | Web users | Brief, contextual | "What do I do here?" |
| `docs/user/` (MkDocs) | Web users | Comprehensive, conceptual | "Help me understand" |
| `docs/*.md` (repo-only) | Developers/operators | Technical | Setup, architecture, internals |

**Rules:**

- In-app and MkDocs docs complement — never duplicate. In-app says "do this";
  docs say "understand why".
- **`docs/user/` must NEVER reference CLI, `.env`, environment variables, or
  operator-level setup.** Web users configure everything through the UI. If a
  feature requires CLI-only setup, it is not ready for user docs.
- **CLI documentation belongs in `docs/*.md` (repo-only) or `CLAUDE.md`.** The CLI
  is a single-operator developer tool. Its docs target developers, not end users.
- The CLI may be deprecated in the future. Do not add new CLI-specific user docs.

### When Adding a Strategy

New strategies automatically appear in the web UI (driven by `StrategyMetadata`).
For docs, update:
- `docs/user/trading-concepts.md` — add plain-English explanation of the strategy
- `docs/user/glossary.md` — add any new terms

### Commands

```bash
just docs          # Build MkDocs site to site/
just docs-serve    # Serve docs locally at http://127.0.0.1:8000
```
