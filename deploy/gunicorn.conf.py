"""Gunicorn configuration for production deployment."""

# Bind to all interfaces (Caddy reverse-proxies to this)
bind = "0.0.0.0:8000"

# Single worker: SQLite single-writer + in-memory rate limiting
# require a single process for correctness. Scale vertically if needed,
# or switch to Redis-backed rate limiting before adding workers.
workers = 1

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

# Disable control socket (not needed in container)
control_socket_disable = True
