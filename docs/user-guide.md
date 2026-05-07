# AurexTrade — User Guide

## Overview

AurexTrade is an automated gold trading bot that connects to OANDA for forex/CFD trading.
It uses rule-based strategies to generate trading signals, applies risk management checks,
and executes trades on your behalf.

## Prerequisites

- **Python 3.12+** — [python.org/downloads](https://www.python.org/downloads/)
- **uv** — Python package manager — install with `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **just** — Task runner — install with `brew install just` (macOS) or see [github.com/casey/just](https://github.com/casey/just)
- **OANDA Account** — For paper/live trading (not needed for local mode)

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
| `paper` | OANDA practice account. Real market data, simulated money. | Yes |
| `live` | Real trading with real capital. **Use with extreme caution.** | Yes |

### Key Settings

| Variable | Default | Description |
|---|---|---|
| `TRADING_MODE` | `local` | Operating mode (`local`, `paper`, `live`) |
| `SYMBOL` | `XAU_USD` | Instrument to trade (gold spot CFD) |
| `INTERVAL_SECONDS` | `60` | How often the bot checks for signals (seconds) |
| `RISK_MAX_POSITION_SIZE` | `10` | Maximum units to hold |
| `RISK_MAX_DAILY_LOSS` | `500.0` | Stop trading if daily loss exceeds this (USD) |
| `RISK_KILL_SWITCH` | `false` | Emergency stop — halts ALL trading immediately |
| `RISK_REQUIRE_STOP_LOSS` | `true` | Reject signals without a stop-loss |
| `RISK_RISK_PER_TRADE` | `0.02` | Risk per trade as fraction of equity (0.02 = 2%) |
| `RISK_MAX_DRAWDOWN_PCT` | `0.20` | Stop if drawdown exceeds this (0.20 = 20%) |
| `RISK_MAX_CONSECUTIVE_LOSSES` | `5` | Pause trading after N consecutive losses |

### OANDA Connection

| Variable | Default | Description |
|---|---|---|
| `OANDA_ACCESS_TOKEN` | *(required)* | API access token from OANDA |
| `OANDA_ACCOUNT_ID` | *(required)* | Your OANDA account ID (e.g., `101-001-12345678-001`) |
| `OANDA_SERVER` | `practice` | `practice` = demo, `live` = real money |

## Setting Up OANDA (Practice Trading)

1. **Create an OANDA practice account** at [oanda.com](https://www.oanda.com/)
2. **Generate an API access token**:
   - Log into the OANDA Account Management Portal
   - Navigate to "Manage API Access" (under "My Services")
   - Click "Generate" to create a personal access token
   - Copy the token — it is shown only once
3. **Find your account ID**:
   - In the Account Management Portal, your account ID is shown on the main page
   - It looks like `101-001-12345678-001`
4. **Set environment variables** in your `.env` file:
   ```
   TRADING_MODE=paper
   OANDA_ACCESS_TOKEN=your-token-here
   OANDA_ACCOUNT_ID=101-001-12345678-001
   OANDA_SERVER=practice
   ```

## Running the Bot

```bash
# Local mode (no broker needed — great for development)
just run

# OANDA practice mode (requires OANDA credentials in .env)
just run-oanda-practice

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

## Web Interface

```bash
just web        # Start web server at http://127.0.0.1:8000
```

For a full walkthrough of the web interface (aimed at non-technical users), see
the **[Web Guide](user/index.md)**.

### API Access (Developers)

The web layer exposes a REST API for programmatic use:

- `POST /api/backtest` — Submit a backtest (`strategy`, `params`, plus config)
- `POST /api/sweep` — Submit a parameter sweep
- `POST /api/walk-forward` — Submit walk-forward validation
- `GET /api/strategies` — List all strategies with parameter metadata

All run endpoints return a `task_id` for polling via `GET /api/{type}/{task_id}`.

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

### "Connection failed" when running in paper mode
- Verify your `OANDA_ACCESS_TOKEN` is valid and not expired
- Verify your `OANDA_ACCOUNT_ID` is correct
- Check that `OANDA_SERVER` matches your account type (`practice` or `live`)

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
