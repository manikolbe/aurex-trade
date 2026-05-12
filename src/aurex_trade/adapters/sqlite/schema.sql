-- AurexTrade SQLite schema
-- Auto-applied on first run by SQLiteRepository.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS signals (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    strength    REAL NOT NULL,
    metadata    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS decisions (
    signal_id   TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    order_id    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    commission  REAL NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    user_id         TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    quantity        REAL NOT NULL,
    average_cost    REAL NOT NULL,
    market_value    REAL NOT NULL,
    unrealized_pnl  REAL NOT NULL,
    realized_pnl    REAL NOT NULL,
    timestamp       TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_signals_user_symbol_timestamp
    ON signals (user_id, symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_trades_user_symbol_timestamp
    ON trades (user_id, symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_decisions_user_timestamp
    ON decisions (user_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_positions_user_symbol
    ON positions (user_id, symbol);

-- Authentication tables

CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    avatar_url  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    last_login  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

-- Market data tables

CREATE TABLE IF NOT EXISTS bars (
    symbol      TEXT NOT NULL,
    granularity TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      REAL NOT NULL,
    PRIMARY KEY (symbol, granularity, timestamp)
);

CREATE TABLE IF NOT EXISTS user_data_preferences (
    user_id     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    granularity TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol, granularity)
);

-- Per-user backtest defaults

CREATE TABLE IF NOT EXISTS user_strategy_defaults (
    user_id         TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,
    params_json     TEXT NOT NULL DEFAULT '{}',
    is_preferred    INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (user_id, strategy_name)
);

CREATE TABLE IF NOT EXISTS user_risk_defaults (
    user_id         TEXT PRIMARY KEY,
    settings_json   TEXT NOT NULL DEFAULT '{}',
    updated_at      TEXT NOT NULL
);

-- Encrypted broker credentials (per-user isolation)

CREATE TABLE IF NOT EXISTS broker_credentials (
    user_id            TEXT NOT NULL,
    broker             TEXT NOT NULL,
    encrypted_data     BLOB NOT NULL,
    account_id_masked  TEXT NOT NULL,
    server             TEXT NOT NULL DEFAULT 'practice',
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, broker)
);
