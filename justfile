# aurexTrade — Task Runner
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

# Install/sync dependencies
sync:
    uv sync

# Clean build artifacts
clean:
    rm -rf dist/ build/ .eggs/ *.egg-info/
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
