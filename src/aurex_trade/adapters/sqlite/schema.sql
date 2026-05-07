-- AurexTrade SQLite schema
-- Auto-applied on first run by SQLiteRepository.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS signals (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    strength    REAL NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS decisions (
    signal_id   TEXT PRIMARY KEY,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    commission  REAL NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    symbol          TEXT PRIMARY KEY,
    quantity        REAL NOT NULL,
    average_cost    REAL NOT NULL,
    market_value    REAL NOT NULL,
    unrealized_pnl  REAL NOT NULL,
    realized_pnl    REAL NOT NULL,
    timestamp       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp
    ON trades (symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_timestamp
    ON signals (symbol, timestamp);
