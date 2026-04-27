# aurexTrade

Automated gold trading bot using Interactive Brokers. Rule-based strategies with
risk management, paper trading, and a path to live trading.

## Quick Start

```bash
# Install dependencies
just sync

# Copy and edit configuration
cp .env.example .env

# Run in local mode (no broker needed)
just run

# Run all checks
just check
```

## Documentation

- **[User Guide](docs/user-guide.md)** — Setup, configuration, running
- **[Architecture](docs/architecture.md)** — Hexagonal design, data flow, schemas
- **[CLAUDE.md](CLAUDE.md)** — LLM onboarding and conventions

## Operating Modes

| Mode | Description | Broker Required? |
|---|---|---|
| `local` | Simulated data, no broker | No |
| `paper` | IBKR paper trading | Yes |
| `live` | Real trading (double-gated) | Yes |
