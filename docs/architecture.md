# aurexTrade — Architecture Reference

## Hexagonal Architecture (Ports & Adapters)

aurexTrade uses hexagonal architecture to keep business logic independent of
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

              Domain Core (center — no external deps):
              ├── models.py (BarData, Signal, Order, Trade, Position)
              ├── strategy/ (Strategy Protocol, indicators, SMA Crossover, RSI Mean-Reversion)
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

## Strategies

All strategies satisfy the `Strategy` Protocol (see `docs/strategies.md` for details):

- **SMA Crossover** — trend-following, buys when short MA crosses above long MA
- **RSI Mean-Reversion** — counter-trend, buys when RSI crosses below oversold

Strategies are pure — they take price bars in and return a signal.
They have no side effects and no external dependencies.
Shared indicators live in `domain/strategy/indicators.py`.

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
```

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
    └── sma_short_window, sma_long_window, atr_multiplier, atr_period
```

Environment variable mapping uses prefixes:
- `OANDA_ACCESS_TOKEN` → `config.oanda.access_token`
- `RISK_MAX_DAILY_LOSS` → `config.risk.max_daily_loss`
- `STRATEGY_SMA_SHORT_WINDOW` → `config.strategy.sma_short_window`

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
    strategy = SMACrossover(config.strategy)
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
