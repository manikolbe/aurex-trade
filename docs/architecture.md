# AurexTrade — Architecture Reference

## Hexagonal Architecture (Ports & Adapters)

AurexTrade uses hexagonal architecture to keep business logic independent of
infrastructure concerns. This means the trading strategy and risk engine know
nothing about OANDA, SQLite, or any other external system.

```
                    ┌─────────────────────────────────┐
                    │         Application Layer        │
                    │     (app.py — composition root)  │
                    │     (TradingEngine — main loop)  │
                    └──────────┬──────────────────────┘
                               │ depends on ports (injected)
              ┌────────────────┼────────────────────┐
              ▼                ▼                     ▼
        ┌──────────┐    ┌──────────────┐     ┌────────────┐
        │BrokerPort│    │MarketDataPort│     │ Repository │
        │(Protocol)│    │  (Protocol)  │     │    Port    │
        └────┬─────┘    └──────┬───────┘     └─────┬──────┘
             │                 │                    │
    ┌────────┴────────┐  ┌────┴─────────┐   ┌─────┴──────┐
    │ OANDABroker     │  │ OANDAMarket  │   │  SQLite    │
    │ PaperBroker     │  │ DataAdapter  │   │ Repository │
    └─────────────────┘  └──────────────┘   └────────────┘

        ┌─────────────────┐
        │CredentialStore  │
        │    Port         │
        └────┬────────────┘
             │
        ┌────┴─────────────┐
        │ FernetCredential │
        │ Store (SQLite)   │
        └──────────────────┘

              Domain Core (center — no external deps):
              ├── models.py (BarData, Signal, Order, Trade, Position)
              ├── strategy/ (Strategy Protocol, Ciby Sliding Grid, Ciby Hedged Doubling Grid)
              └── risk/ (RiskEngine)
```

### Why Hexagonal?

1. **Broker swappability** — OANDA can be replaced without touching strategy or risk logic
2. **Testability** — domain logic tested in isolation, no mocks of external services needed
3. **Safety** — financial logic can't accidentally depend on infrastructure details
4. **Future-proofing** — adding REST API, new brokers, or PostgreSQL only requires new adapters

## Data Flow

### Main Trading Loop

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  Market  │     │ Strategy │     │   Risk   │     │ Broker   │     │  Persist │
│  Data    │────▶│ Generate │────▶│ Evaluate │────▶│ Execute  │────▶│  Store   │
│  (Port)  │     │ Signal   │     │ Decision │     │ Order    │     │  (Port)  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘     └──────────┘
                                       │
                                       │ REJECTED?
                                       ▼
                                  Log & skip
```

**Every signal passes through risk.** There is no code path that bypasses the
risk engine. This is a non-negotiable safety invariant.

### Sequence (one cycle)

1. `TradingEngine` calls `MarketDataPort.get_latest_bars(symbol, count)`
2. `Strategy.generate(bars)` returns `Signal | None`
3. If signal exists, `RiskEngine.evaluate(signal, current_position)` returns `RiskDecision`
4. If approved, `TradingEngine` creates an `Order` and calls `BrokerPort.place_order(order)`
5. Broker returns `Trade` (or error)
6. `RepositoryPort` saves signal, decision, trade, and updated position
7. Engine sleeps for `interval_seconds`, then repeats

## Domain Models

All models are **frozen dataclasses** — immutable after creation.

```
BarData
├── timestamp: datetime (UTC)
├── open, high, low, close: float
├── volume: float
└── symbol: str

Signal
├── id: UUID
├── timestamp: datetime (UTC)
├── symbol: str
├── signal_type: SignalType (LONG | SHORT | FLAT)
├── strategy_name: str
├── strength: float (0.0 to 1.0)
└── metadata: dict[str, str]

RiskDecision
├── signal_id: UUID
├── action: RiskAction (APPROVED | REJECTED | KILL_SWITCH)
├── reason: str
└── timestamp: datetime (UTC)

Order
├── id: UUID
├── signal_id: UUID
├── symbol: str
├── side: OrderSide (BUY | SELL)
├── quantity: float
├── status: OrderStatus (PENDING → SUBMITTED → FILLED | CANCELLED | REJECTED)
└── timestamp: datetime (UTC)

Trade
├── id: UUID
├── order_id: UUID
├── symbol, side, quantity, price, commission
└── timestamp: datetime (UTC)

Position
├── symbol: str
├── quantity, average_cost, market_value
├── unrealized_pnl, realized_pnl
└── timestamp: datetime (UTC)
```

## Port Interfaces

Ports are Python `Protocol` classes — structural subtyping means adapters don't
need to explicitly inherit from the port. They just need matching method signatures.

### BrokerPort
```python
class BrokerPort(Protocol):
    def place_order(self, order: Order) -> Trade: ...
    def cancel_order(self, order_id: UUID) -> bool: ...
    def get_positions(self, symbol: str) -> Position | None: ...
```

### MarketDataPort
```python
class MarketDataPort(Protocol):
    def get_latest_bars(self, symbol: str, count: int) -> list[BarData]: ...
```

### RepositoryPort
```python
class RepositoryPort(Protocol):
    def save_signal(self, signal: Signal) -> None: ...
    def save_decision(self, decision: RiskDecision) -> None: ...
    def save_trade(self, trade: Trade) -> None: ...
    def save_position(self, position: Position) -> None: ...
    def get_trades_today(self, symbol: str) -> list[Trade]: ...
    def get_current_position(self, symbol: str) -> Position | None: ...
```

### HistoricalDataPort
```python
class HistoricalDataPort(Protocol):
    def save_bars(self, bars: list[BarData], symbol: str, granularity: str) -> None: ...
    def load_bars(self, symbol: str, granularity: str, start=None, end=None) -> list[BarData]: ...
    def get_date_range(self, symbol: str, granularity: str) -> tuple[datetime, datetime] | None: ...
```

### RunStorePort
```python
class RunStorePort(Protocol):
    def start_run(self, run_id: str, *, user_id: str, strategy: str, ...) -> None: ...
    def finish_run(self, run_id: str, *, user_id: str, net_realized_pnl: float, ...) -> None: ...
```
Durable per-run summary (config + outcome + net P&L). Injected optionally into the
engine; `None` ⇒ no-op (CLI/tests). See `docs/log-analysis.md`.

## Adapter Implementations

### Paper Adapter (`adapters/paper/`)
- Implements BrokerPort + MarketDataPort
- Simulates order fills at current market price
- Generates random-walk price data (seeded for deterministic testing)
- Tracks positions in memory
- Used for `TRADING_MODE=local`

### In-Memory Repository (`adapters/memory/`)
- Implements RepositoryPort
- Stores signals, decisions, trades, and positions in plain dicts/lists
- No external dependencies, no disk I/O
- Used for fast unit/integration tests
- For runtime persistence, see SQLite adapter below

### OANDA Adapter (`adapters/oanda/`)
- Uses `httpx` to call the OANDA v20 REST API directly
- `OANDAConnection` wraps httpx.Client with auth headers and base URL
- `OANDABrokerAdapter` implements BrokerPort (market orders, position queries)
- `OANDAMarketDataAdapter` implements MarketDataPort (historical candles)
- Validates credentials on connect by calling the accounts endpoint
- Used for `TRADING_MODE=paper` and `TRADING_MODE=live`

### SQLite Adapter (`adapters/sqlite/`)
- Uses Python stdlib `sqlite3` (no ORM)
- WAL mode enabled for safe concurrent reads
- Parameterized queries only (SQL injection prevention)
- Schema auto-created on first run via `schema.sql`
- Used for all trading modes — data persists across restarts
- DB path configurable via `DB_PATH` (default: `data/aurex_trade.db`)
- `SQLiteMarketDataStore` — implements `HistoricalDataPort`, stores bars in
  a shared `bars` table with `INSERT OR IGNORE` for concurrent-safe writes.
  Used by both CLI and web for historical market data.
- `UserDataPreferencesStore` — per-user date range preferences for the
  backtest UI, stored in `user_data_preferences` table.
- `SQLiteRunStore` — implements `RunStorePort`, writes one durable summary row per
  engine run to the `bot_runs` table (config + outcome + net P&L), so run history
  survives log rotation. It is a rollup, not an event log; the structured JSON log
  remains the authoritative event-sourced record. See `docs/log-analysis.md`.

## Strategies

All strategies satisfy the `Strategy` Protocol (see `docs/strategies.md` for details):

- **Ciby Sliding Grid** — primary, live strategy; hedged-pair grid that slides its
  active band as price trends
- **Ciby Hedged Doubling Grid** — experimental; hedged grid with a doubling
  mechanism at outer levels

Strategies are pure — they take price bars in and return a signal.
They have no side effects and no external dependencies.

## Risk Engine

The risk engine is the **mandatory gate** between strategy signals and order execution.

### Rules (all checked, in priority order)

1. **Kill switch** — if `RISK_KILL_SWITCH=true`, reject everything immediately
2. **Stop-loss enforcement** — reject if signal has no stop-loss (when `RISK_REQUIRE_STOP_LOSS=true`)
3. **Max drawdown** — reject if equity drawdown from peak exceeds `RISK_MAX_DRAWDOWN_PCT`
4. **Consecutive losses** — reject if last N trades were all losers (`RISK_MAX_CONSECUTIVE_LOSSES`)
5. **Max position size** — reject if resulting position would exceed `RISK_MAX_POSITION_SIZE`
6. **Max daily loss** — reject if today's realized + unrealized P&L is below `-RISK_MAX_DAILY_LOSS`
7. **Trade frequency** — reject if already executed `RISK_MAX_TRADES_PER_DAY` trades today

If any rule rejects, the entire signal is rejected with a logged reason.

### Position Sizing

Units are calculated dynamically: `units = (equity * risk_per_trade) / stop_distance`,
capped at `max_position_size`.

## Database Schema

See `src/aurex_trade/adapters/sqlite/schema.sql` for the authoritative schema.
WAL mode is enabled for safe concurrent reads.

```sql
CREATE TABLE signals (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    strength    REAL NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE decisions (
    signal_id   TEXT PRIMARY KEY,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE trades (
    id          TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    commission  REAL NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE positions (
    symbol          TEXT PRIMARY KEY,
    quantity        REAL NOT NULL,
    average_cost    REAL NOT NULL,
    market_value    REAL NOT NULL,
    unrealized_pnl  REAL NOT NULL,
    realized_pnl    REAL NOT NULL,
    timestamp       TEXT NOT NULL
);

-- Durable per-run summary (rollup, not an event log). One row per engine run;
-- 'running' until engine_stopped finalizes it. See docs/log-analysis.md.
CREATE TABLE bot_runs (
    run_id            TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    strategy          TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    interval          INTEGER NOT NULL,
    strategy_params   TEXT NOT NULL DEFAULT '{}',
    risk_params       TEXT NOT NULL DEFAULT '{}',
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    stop_reason       TEXT,
    total_cycles      INTEGER,
    sessions          INTEGER,
    closures          INTEGER,
    net_realized_pnl  REAL,
    initial_equity    REAL,
    final_equity      REAL
);
```

(Tables shown without `user_id` for brevity carry it in the real schema for
multi-tenant isolation; see `schema.sql`.)

## Configuration

Configuration uses Pydantic Settings with nested models:

```
AppConfig
├── trading_mode: TradingMode
├── symbol: str
├── interval_seconds: int
├── db_path: Path
├── log_level: str
├── live_trading_confirmed: bool
├── oanda: OANDAConfig
│   ├── access_token, account_id, server
├── risk: RiskConfig
│   ├── max_position_size, max_daily_loss
│   ├── max_trades_per_day, kill_switch
│   ├── require_stop_loss, risk_per_trade
│   └── max_drawdown_pct, max_consecutive_losses
└── strategy: StrategyConfig
    └── grid_spacing, anchor_gap, buy_sell_offset, anchor_units, grid_units,
        stop_buffer, max_levels_ahead, max_levels_behind, session_profit_target,
        session_loss_limit, daily_loss_limit
```

Environment variable mapping uses prefixes:
- `OANDA_ACCESS_TOKEN` → `config.oanda.access_token`
- `RISK_MAX_DAILY_LOSS` → `config.risk.max_daily_loss`
- `STRATEGY_GRID_SPACING` → `config.strategy.grid_spacing`

## Web Layer (`web/`)

The web layer is a FastAPI application serving both a JSON API and an HTMX-driven UI.
It acts as a second composition root (alongside the CLI `app.py`), wiring adapters
for the multi-user web context.

### Transport Separation

Routers are organized into feature-based modules with explicit transport separation:

- **`api.py`** — JSON in, JSON out. Pydantic request/response models. No templates.
- **`htmx.py`** — Form data in, HTML fragments out. Jinja2 template rendering.
- **`_common.py`** — Shared constants/validation within a feature (if needed).

Each feature folder exports a single combined `router` from its `__init__.py`.
The app includes one router per feature — no cross-feature imports between routers.

### Request Flow

```
Browser → FastAPI → AuthMiddleware → Router (api.py or htmx.py)
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼             ▼
                   CredentialStore  TaskRegistry  UserDefaults
                   (per-user)      (background)  (per-user)
```

### Multi-User Isolation

The web layer is designed for multi-user access (Google OAuth + session cookies).
Every data access is scoped to the authenticated user — user A cannot see user B's
credentials, preferences, or task results. This is enforced by the `get_current_user`
dependency injected into all authenticated endpoints.

## Composition Root (`app.py`)

The composition root is the ONLY place that knows about concrete adapter classes.
It reads configuration, instantiates the appropriate adapters based on `TRADING_MODE`,
injects them into the `TradingEngine`, and starts the main loop.

```python
# Pseudocode — app.py
def main():
    config = AppConfig()

    if config.trading_mode == TradingMode.LIVE:
        if not config.live_trading_confirmed:
            raise SystemExit("LIVE trading requires LIVE_TRADING_CONFIRMED=true")

    # Select adapters based on mode
    match config.trading_mode:
        case TradingMode.LOCAL:
            broker = PaperBrokerAdapter()
            market_data = PaperMarketDataAdapter()
        case TradingMode.PAPER | TradingMode.LIVE:
            connection = OANDAConnection(config.oanda)
            broker = OANDABrokerAdapter(connection)
            market_data = OANDAMarketDataAdapter(connection)

    repository = SQLiteRepository(config.db_path)
    strategy = CibySlidingGridStrategy(config.strategy)
    risk_engine = RiskEngine(config.risk)

    engine = TradingEngine(
        strategy=strategy,
        risk_engine=risk_engine,
        broker=broker,
        market_data=market_data,
        repository=repository,
        config=config,
    )
    engine.run()
```

---

## Rate Limiting

API endpoints are rate-limited per-IP using [slowapi](https://github.com/laurentS/slowapi)
to prevent resource exhaustion and abuse.

### Configuration

All settings are configurable via environment variables (prefix: `RATELIMIT_`):

| Env Var | Default | Purpose |
|---------|---------|---------|
| `RATELIMIT_ENABLED` | `true` | Kill switch — set to `false` to disable all rate limiting |
| `RATELIMIT_STORAGE_URI` | `memory://` | Storage backend URI |
| `RATELIMIT_DEFAULT` | `60/minute` | Global default for all endpoints |
| `RATELIMIT_COMPUTE` | `5/minute` | CPU-intensive: backtest, sweep, walk-forward |
| `RATELIMIT_BOT_CONTROL` | `3/minute` | Critical controls: bot start/stop |
| `RATELIMIT_READ` | `120/minute` | Read endpoints (polling, status checks) |
| `RATELIMIT_AUTH` | `10/minute` | OAuth endpoints (google redirect, callback) |
| `RATELIMIT_AUTH_LOGOUT` | `5/minute` | Logout |

**Proxy requirement:** When deployed behind a reverse proxy, the proxy must
**overwrite** (not append to) the `X-Forwarded-For` header to prevent IP spoofing:

```caddyfile
# Caddy — overwrite mode (prevents client spoofing)
header_up X-Forwarded-For {remote_host}
```

Without this, clients can send arbitrary `X-Forwarded-For` values to get
independent rate limit buckets.

### Endpoint Limits

| Endpoint | Limit | Reason |
|----------|-------|--------|
| `POST /api/backtest`, `/api/sweep`, `/api/walk-forward` | 5/min | CPU-intensive (2 ThreadPool workers) |
| `POST /api/bot/start`, `/api/bot/stop` | 3/min | Critical controls |
| `GET /api/*` (polling) | 120/min | HTMX polls every 2-5s |
| `GET /auth/google`, `/auth/callback` | 10/min | OAuth abuse prevention |
| `POST /auth/logout` | 5/min | CSRF flooding prevention |
| `GET /api/health` | Exempt | Load balancer / monitoring |

### 429 Response Format

Rate-limited requests receive:
- `Retry-After` header (seconds until retry is allowed)
- JSON body for `/api/*` routes: `{"error": "Rate limit exceeded", "detail": "Try again in N seconds", "status_code": 429}`
- HTML fragment for `/htmx/*` routes (DaisyUI warning alert)

### Frontend Behavior

The HTMX frontend (`static/ratelimit.js`) intercepts 429 responses client-side:
1. Suppresses the error (does not show to user)
2. Reads `Retry-After` header for delay timing
3. Retries the request automatically (up to 3 attempts with exponential backoff)
4. Only surfaces the error if all retries are exhausted

Normal HTMX polling (every 2-5s = ~12-30 req/min) stays well under the 120/min
read limit and is never disrupted.

### Scaling Constraint

> **Hard blocker for horizontal scaling:** Rate limit state is stored in-memory
> (single-process only). Without shared storage, scaling breaks rate limiting:
>
> - **Single-node, single-worker:** Works as designed
> - **Multi-worker (`uvicorn --workers > 1`):** Each worker has independent
>   counters — allows N× the intended rate
> - **Multi-node (horizontal scaling):** Same problem across machines
>
> **To enable multi-worker or multi-node scaling:**
> 1. Deploy Redis (or Memcached/MongoDB)
> 2. Set `RATELIMIT_STORAGE_URI=redis://host:6379`
> 3. Add `redis` to Python dependencies
>
> The slowapi/limits library natively supports Redis, Memcached, and MongoDB as
> storage backends — switching is a configuration change, not a code change.

## Deployment Architecture

Production deployment uses Docker Compose with two services:

```
Client → Caddy (TLS + headers + proxy) → App (gunicorn + uvicorn workers)
              :80/:443                         :8000 (internal only)
```

### Container Layout

| Service | Image | Role |
|---------|-------|------|
| `app` | Custom (multi-stage Dockerfile) | FastAPI via gunicorn + UvicornWorker |
| `caddy` | `caddy:2-alpine` | Reverse proxy, TLS termination, security headers |

### Dockerfile (multi-stage)

- **Builder stage**: Installs all deps (including dev) to build MkDocs documentation
- **Runtime stage**: Production deps only + gunicorn. Source stays at `/app/src/`
  with `PYTHONPATH=/app/src` (no editable install — path resolution relies on
  `Path(__file__).parent` landing in the source tree)

### Caddy Responsibilities

- Automatic TLS via Let's Encrypt (when `CADDY_DOMAIN` is a real domain)
- HTTP-only on port 80 for local dev (default: `CADDY_DOMAIN=:80`)
- Security headers on ALL responses (CSP, HSTS, X-Frame-Options, etc.)
- `X-Forwarded-For` overwrite (prevents IP spoofing for rate limiting)
- Request logging (JSON to stdout)

### Gunicorn Configuration (`deploy/gunicorn.conf.py`)

- Single worker (SQLite single-writer + in-memory rate limiting require one process)
- `UvicornWorker` class for async/ASGI support
- Factory pattern: `aurex_trade.web.app:create_app()`

### Volume Strategy

| Volume | Mount | Purpose |
|--------|-------|---------|
| `app-data` | `/app/data` | SQLite database (persistent across deploys) |
| `caddy-data` | `/data` | TLS certificates (auto-managed by Caddy) |
| `caddy-config` | `/config` | Caddy internal state |

### Container Hardening

- `no-new-privileges` on both containers (prevents privilege escalation)
- `cap_drop: ALL` — all Linux capabilities removed
- Caddy gets `NET_BIND_SERVICE` only (needed for ports 80/443)
- App container: no resource limits (backtests need full CPU/RAM)
- Caddy container: 128M memory limit
- Log rotation: 10MB x 3 files per container (prevents disk exhaustion)
- App runs as `appuser` (uid 1000), never root
- `.env` file permissions: 600 (owner-only read)

### Environment

- `CADDY_DOMAIN` — `:80` for local, real domain for production (triggers auto-TLS)
- `WEB_HOST=0.0.0.0` — required for container networking
- All other config via `.env` file (passed to app only; Caddy only receives `CADDY_DOMAIN`)

### Production Instance

- **URL**: `https://aurex.manikolbe.com`
- **VPS**: Hetzner CPX22 (2 vCPU, 4GB RAM, 80GB SSD), Nuremberg
- **DNS**: Cloudflare (DNS-only mode, no proxy)
- **Firewall**: Hetzner Cloud Firewall — SSH (home IP only), HTTP/HTTPS (all)
- **SSH**: Key-only, root disabled, `deploy` user with docker group access
- **Backups**: Hetzner automated backups enabled
