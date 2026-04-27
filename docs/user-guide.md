# aurexTrade — User Guide

## Overview

aurexTrade is an automated gold trading bot that connects to Interactive Brokers (IBKR).
It uses rule-based strategies to generate trading signals, applies risk management checks,
and executes trades on your behalf.

## Prerequisites

- **Python 3.12+** — [python.org/downloads](https://www.python.org/downloads/)
- **uv** — Python package manager — install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **just** — Task runner — install with `brew install just` (macOS) or see [github.com/casey/just](https://github.com/casey/just)
- **IBKR Account** — For paper/live trading (not needed for local mode)

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd aurex-trade

# Install dependencies
just sync
# or: uv sync

# Copy environment config
cp .env.example .env
```

## Configuration

Edit `.env` to configure the bot. All settings have safe defaults.

### Operating Modes

| Mode | Description | Broker Required? |
|---|---|---|
| `local` | Simulated data, no broker connection. For development and testing. | No |
| `paper` | IBKR paper trading account. Real market data, simulated money. | Yes |
| `live` | Real trading with real capital. **Use with extreme caution.** | Yes |

### Key Settings

| Variable | Default | Description |
|---|---|---|
| `TRADING_MODE` | `local` | Operating mode (`local`, `paper`, `live`) |
| `SYMBOL` | `GLD` | Instrument to trade (SPDR Gold ETF) |
| `INTERVAL_SECONDS` | `60` | How often the bot checks for signals (seconds) |
| `RISK_MAX_POSITION_SIZE` | `10` | Maximum shares to hold |
| `RISK_MAX_DAILY_LOSS` | `500.0` | Stop trading if daily loss exceeds this (USD) |
| `RISK_KILL_SWITCH` | `false` | Emergency stop — halts ALL trading immediately |

### IBKR Connection

aurexTrade does **not** store your IBKR credentials. Authentication is handled
entirely by TWS or IB Gateway — the bot only needs connection details:

| Variable | Default | Description |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | TWS/Gateway host (always localhost for local dev) |
| `IBKR_PORT` | `7497` | `7497` = paper trading, `7496` = live |
| `IBKR_CLIENT_ID` | `1` | Unique integer per concurrent connection |

## Setting Up IBKR (Paper Trading)

1. **Create an IBKR paper trading account** at [interactivebrokers.com](https://www.interactivebrokers.com/)
2. **Download TWS** (Trader Workstation) or **IB Gateway**
   - TWS: Full trading platform with GUI
   - IB Gateway: Lightweight, headless — recommended for automated trading
3. **Enable API access**:
   - In TWS: Edit → Global Configuration → API → Settings
   - Check "Enable ActiveX and Socket Clients"
   - Set port to `7497` (paper) or `7496` (live)
   - Check "Allow connections from localhost only"
4. **Start TWS/Gateway** and log in with your paper trading credentials
5. **Set `TRADING_MODE=paper`** in your `.env` file

## Running the Bot

```bash
# Local mode (no broker needed — great for development)
just run

# Paper trading mode (requires TWS/Gateway running)
just run-paper

# Or set mode in .env and run:
just run
```

### Stopping the Bot

- Press `Ctrl+C` to gracefully stop
- Or set `RISK_KILL_SWITCH=true` in `.env` for emergency stop

## Understanding the Output

The bot logs all decisions to the console and to files in `logs/`.

### Log Levels

| Level | What it shows |
|---|---|
| `DEBUG` | Everything — market data, calculations, internal state |
| `INFO` | Signals, risk decisions, trades, position changes |
| `WARNING` | Risk rejections, connection issues, unusual conditions |
| `ERROR` | Failures that skip a trading cycle |

### Decision Flow

Each cycle, the bot:
1. **Fetches market data** — latest price bars for the configured symbol
2. **Generates signal** — strategy analyzes data and produces a signal (LONG/SHORT/FLAT)
3. **Risk check** — risk engine evaluates the signal against position limits, daily loss, etc.
4. **Executes** — if approved, places an order with the broker
5. **Persists** — saves signal, decision, and trade to the database

## Development

```bash
just check      # Run all checks (lint + typecheck + test)
just test       # Run tests only
just lint       # Run linter only
just typecheck  # Run type checker only
just fmt        # Auto-format code
just clean      # Remove build artifacts
```

## Troubleshooting

### "Connection refused" when running in paper mode
- Ensure TWS or IB Gateway is running
- Check that API is enabled (see IBKR setup above)
- Verify `IBKR_PORT` matches the port in TWS/Gateway settings

### "Kill switch activated"
- Set `RISK_KILL_SWITCH=false` in `.env` to resume trading
- Check logs to understand why it was activated

### Tests failing
- Run `just sync` to ensure dependencies are up to date
- Run `just check` to see all errors (lint, types, tests)

## Safety Checklist (Before Live Trading)

**Do NOT enable live trading until you have:**

- [ ] Run in paper mode for at least 2 weeks
- [ ] Reviewed all trades and verified the strategy behaves as expected
- [ ] Set appropriate risk limits (position size, daily loss)
- [ ] Tested the kill switch
- [ ] Reviewed all logs for unexpected behavior
- [ ] Understood the financial risks — automated trading can lose real money
- [ ] Set both `TRADING_MODE=live` AND `LIVE_TRADING_CONFIRMED=true`
