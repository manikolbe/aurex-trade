# Stage 1: Build documentation (requires dev dependencies for mkdocs)
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install deps first (cached unless pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

# Copy source and install project
COPY src/ src/
RUN uv sync --frozen

# Build MkDocs user documentation
COPY docs/user/ docs/user/
COPY mkdocs.yml ./
RUN uv run mkdocs build


# Stage 2: Production runtime
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install production dependencies only (no project install — we use PYTHONPATH)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

# Install gunicorn into the venv (deployment concern, not a project dependency)
RUN uv pip install --no-cache-dir gunicorn

# Copy application source
COPY src/ src/

# Copy built docs from builder stage
COPY --from=builder /app/site site/

# Copy gunicorn configuration
COPY deploy/gunicorn.conf.py ./

# Runtime environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    HOME=/app

# Create data/logs directories and non-root user (UID 1000 for volume consistency)
RUN mkdir -p data logs \
    && useradd --no-create-home --uid 1000 appuser \
    && chown appuser:appuser data logs

USER appuser

EXPOSE 8000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "aurex_trade.web.app:create_app()"]
