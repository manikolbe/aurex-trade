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
│   │   ├── indicators.py   # Shared: calculate_atr()
│   │   ├── sma_crossover.py    # SMA Crossover implementation
│   │   └── rsi_mean_reversion.py  # RSI Mean-Reversion implementation
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
# Bot config (strategy + risk params logged at startup):
#   | grep "engine_started"
# Grid levels (anchor + all levels, logged once after first bar):
#   | grep "grid_initialized"
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
just backtest --strategy sma_crossover --param short_window=10 --param long_window=30
just backtest --strategy rsi_mean_reversion --param period=14 --param overbought=70 --param oversold=30
just sweep --strategy sma_crossover --param short_window=5,10,20 --param long_window=20,30,50 --spread 0.6
just walk-forward --strategy rsi_mean_reversion --param period=7,14,21 --param overbought=70,75 --param oversold=25,30
```

## Bot Configuration (Web UI)

The bot is configured entirely through the web UI at https://aurex.manikolbe.com.
Configuration is per-user (stored in SQLite via user preferences).

### Key Settings

| Setting | Description | Test Value | Notes |
|---------|-------------|------------|-------|
| **Strategy** | Trading strategy to use | `ciby_hedged_grid` | Selected from dropdown |
| **Symbol** | Instrument to trade | `XAU_USD` | Gold vs USD |
| **Interval** | Seconds between cycles | `60` | 1 min for testing, 300+ for production |
| **Granularity** | OANDA candle granularity | `M1` | Must align with interval |

### Ciby Hedged Grid Strategy Parameters

| Parameter | Description | Test Value | Production Value |
|-----------|-------------|------------|-----------------|
| `grid_spacing` | Distance between grid levels ($) | `15` | `15` |
| `initial_units` | Units for first pair | `10` | `10` |
| `grid_units` | Units for subsequent pairs | `20` | `20` |
| `stop_distance` | Stop-loss distance ($) | `16` | `16` |
| `session_profit_target` | Close all & restart when hit ($) | `100` | TBD |
| `session_loss_limit` | Close all & restart when hit ($) | `50` | TBD |
| `daily_loss_limit` | Stop trading for the day ($) | `200` | TBD |

### Risk Engine Settings (environment variables)

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_POSITION_SIZE` | Max units per position | `10` |
| `RISK_PER_TRADE` | Fraction of equity risked per trade | `0.02` |
| `MAX_DRAWDOWN_PCT` | Kill switch threshold (%) | `0.10` |
| `MAX_DAILY_LOSS` | Daily loss limit ($) | `500` |

### Starting the Bot for Testing

1. Navigate to web UI → Bot page
2. Select strategy: `ciby_hedged_grid`
3. Set params: `grid_spacing=15`, `initial_units=10`, `grid_units=20`, `stop_distance=16`
4. Set interval: `60` (1 minute cycles for fast feedback)
5. Click Start → bot begins trading on connected OANDA account
6. Monitor via logs: `ssh aurex 'docker compose -f ~/aurex-trade/docker-compose.yml logs -f --tail=10 app'`

### Clean State for Testing

The bot does NOT automatically reconcile pre-existing positions on startup.
If testing closure detection, ensure a clean slate:
- Close all open XAU_USD trades in OANDA before starting
- Or: let the bot start fresh — it will track only trades it opens

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
