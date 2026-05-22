# AurexTrade — Task Runner
# Run `just --list` to see all available commands.

# Default: run all checks
default: check

# Run all checks (lint + typecheck + test)
check: lint typecheck test

# Run tests
test *args='':
    uv run pytest {{args}}

# Run linter
lint:
    uv run ruff check src/ tests/

# Run type checker
typecheck:
    uv run mypy src/

# Format code
fmt:
    uv run ruff format src/ tests/
    uv run ruff check --fix src/ tests/

# Run the bot (local mode by default)
run *args='':
    uv run python -m aurex_trade {{args}}

# Run in OANDA practice mode (requires OANDA credentials in .env)
run-oanda-practice:
    TRADING_MODE=paper uv run python -m aurex_trade

# Download historical data from OANDA
download-data *args='':
    uv run python -m aurex_trade.backtest download-data {{args}}

# Run a backtest
backtest *args='':
    uv run python -m aurex_trade.backtest run {{args}}

# Run parameter sweep (grid search)
sweep *args='':
    uv run python -m aurex_trade.backtest sweep {{args}}

# Run walk-forward validation
walk-forward *args='':
    uv run python -m aurex_trade.backtest walk-forward {{args}}

# Run the web server
web *args='':
    uv run python -m aurex_trade.web {{args}}

# Run the web server in development mode (auto-reload)
web-dev:
    WEB_RELOAD=true uv run python -m aurex_trade.web

# Install/sync dependencies
sync:
    uv sync

# Build user-facing documentation site
docs:
    uv run mkdocs build

# Serve documentation locally for preview
docs-serve:
    uv run mkdocs serve

# Build and start Docker containers (local)
deploy-local:
    docker compose up --build -d

# Stop local Docker containers
deploy-local-down:
    docker compose down

# View local Docker container logs
deploy-local-logs *args='':
    docker compose logs {{args}}

# Deploy to production VPS (requires ssh aurex configured)
deploy-prod:
    ssh aurex 'cd ~/aurex-trade && git pull && docker compose build --build-arg GIT_SHA=$(git rev-parse --short HEAD) && docker compose up -d'

# Clean build artifacts
clean:
    rm -rf dist/ build/ .eggs/ *.egg-info/
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
