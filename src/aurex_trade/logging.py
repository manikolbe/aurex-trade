"""Structured logging configuration — JSON to file, human-readable to console.

Call setup_logging() once at startup (from app.py) before any log statements.
After setup, get loggers via structlog.get_logger() anywhere in the codebase.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


def setup_logging(log_level: str = "INFO", log_dir: Path = Path("logs")) -> None:
    """Configure structlog with console (human-readable) and file (JSON) output.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory for JSON log files. Created if it doesn't exist.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared pre-processing (runs before formatting)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # --- stdlib logging (captures third-party library logs) ---
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "aurex_trade.log"

    # Console handler — human-readable
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    console_handler.setFormatter(console_formatter)

    # File handler — JSON with rotation (10MB per file, keep 10 backups = 110MB max).
    # The event-sourced trade log (signals, fills, closures, sessions) is the only
    # complete record of a run, so retention is sized to comfortably hold several
    # days of activity. This is viable only because the high-volume noise has been
    # removed: the per-poll debug_trade_markers dump (was ~83% of volume) is gone,
    # and the httpx/httpcore client loggers are quieted to WARNING below (~15%).
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    file_handler.setFormatter(file_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(level)

    # Quiet noisy third-party clients. httpx/httpcore log one INFO line per request
    # ("HTTP Request: GET ..."), which floods the file with one line per OANDA poll
    # (every few seconds) and carries no analytical value — only their warnings and
    # errors matter. Pin them to WARNING regardless of the root level.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # --- structlog configuration ---
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
