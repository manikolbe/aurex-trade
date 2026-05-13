"""Gunicorn configuration for production deployment."""

import multiprocessing

# Bind to all interfaces (Caddy reverse-proxies to this)
bind = "0.0.0.0:8000"

# Workers: cap at 4 due to SQLite single-writer constraint
workers = min(2 * multiprocessing.cpu_count() + 1, 4)

# Uvicorn worker class for ASGI/async support
worker_class = "uvicorn.workers.UvicornWorker"

# Timeouts
timeout = 120
graceful_timeout = 30
keepalive = 5

# Logging to stdout/stderr (Docker captures these)
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Security limits
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# Process naming
proc_name = "aurex-trade"

# Preload app for faster worker spawning
preload_app = True
