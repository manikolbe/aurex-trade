# CLAUDE.md — aurexTrade LLM Onboarding

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
├── ports/              # Protocol interfaces (BrokerPort, MarketDataPort, RepositoryPort)
├── adapters/
│   ├── oanda/          # OANDA adapter (httpx → v20 REST API)
│   ├── backtest/       # Backtesting adapters (simulated broker, historical replay)
│   ├── memory/         # In-memory repository (local mode + tests)
│   ├── paper/          # Paper trading simulator
│   └── sqlite/         # SQLite persistence
├── backtest/
│   ├── cli.py          # CLI: download-data, run, sweep, walk-forward
│   ├── runner.py       # BacktestRunner — core orchestration loop
│   ├── sweep.py        # ParameterSweep — grid search
│   └── walk_forward.py # WalkForwardValidator — train/test validation
├── engine/
│   └── trading_engine.py  # Main trading loop
├── web/                # FastAPI app (API + HTMX UI)
└── logging.py          # structlog configuration
```

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

# Backtesting (see docs/backtesting.md for full details)
just download-data --symbol XAU_USD --granularity M1 --start 2025-04-14 --end 2025-04-18
just backtest --strategy sma_crossover --param short_window=10 --param long_window=30
just backtest --strategy rsi_mean_reversion --param period=14 --param overbought=70 --param oversold=30
just sweep --strategy sma_crossover --param short_window=5,10,20 --param long_window=20,30,50 --spread 0.6
just walk-forward --strategy rsi_mean_reversion --param period=7,14,21 --param overbought=70,75 --param oversold=25,30
```

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
   - `generate(bars: list[BarData]) -> Signal | None`
   - `metadata() -> StrategyMetadata` classmethod (with `ParamMeta` for each param)
3. Register in `backtest/cli.py`: add to `STRATEGY_REGISTRY`, `PARAM_VALIDATORS`, `STRATEGY_METADATA`
4. Add tests in `tests/unit/domain/test_your_strategy.py`
5. Verify: `just backtest --strategy your_strategy --param key=value`

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
