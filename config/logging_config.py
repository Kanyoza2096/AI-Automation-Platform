"""
Kanyoza Systems AI Platform v10 — Structured logging configuration.

Every log record includes:
  - timestamp (ISO-8601 UTC)
  - level
  - module / logger name
  - correlation_id (from contextvars, when set)
  - message
  - duration_ms (when caller provides it via extra={"duration_ms": n})
  - exception (when exc_info is set)

JSON format in production; coloured human-readable in development.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from utils.ids import get_current_correlation_id


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class StructuredJsonFormatter(logging.Formatter):
    """
    Emit one compact JSON object per log line.
    Suitable for log aggregators (Datadog, Cloud Logging, Loki, etc.).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "correlation_id": get_current_correlation_id(),
            "msg": record.getMessage(),
        }

        # Duration — opt-in via extra={"duration_ms": n}
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = record.duration_ms

        # Exception
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Any additional extra keys the caller passed
        skip = frozenset(logging.LogRecord.__dict__) | {
            "msg", "args", "levelname", "name", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno",
            "funcName", "created", "msecs", "relativeCreated", "thread",
            "threadName", "processName", "process", "message", "taskName",
            "duration_ms",
        }
        for key, value in record.__dict__.items():
            if key not in skip and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


class DevFormatter(logging.Formatter):
    """
    Coloured, human-readable formatter for local development.
    Shows: [HH:MM:SS] LEVEL  logger.name  correlation_id: message
    """

    _COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    _RESET = "\033[0m"
    _DIM   = "\033[2m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._COLORS.get(record.levelname, "")
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        cid = get_current_correlation_id()
        cid_tag = f" {self._DIM}[{cid[:8]}]{self._RESET}" if cid else ""
        prefix = (
            f"{color}[{ts}] {record.levelname:<8}{self._RESET} "
            f"{self._DIM}{record.name}{self._RESET}{cid_tag}: "
        )
        line = prefix + record.getMessage()
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> logging.Logger:
    """
    Configure application-wide structured logging.
    Call exactly once at startup before any logger is used.

    Returns the root 'kanyoza' logger.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    is_dev = os.getenv("ENVIRONMENT", "production").lower() in ("development", "dev", "local")

    root = logging.getLogger("kanyoza")
    root.setLevel(log_level)
    root.handlers.clear()
    root.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    handler.setFormatter(DevFormatter() if is_dev else StructuredJsonFormatter())
    root.addHandler(handler)

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "requests", "httpx", "werkzeug", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under the 'kanyoza' namespace.

    Usage:
        logger = get_logger(__name__)
        logger.info("Service started", extra={"duration_ms": 42})
    """
    return logging.getLogger(f"kanyoza.{name}" if not name.startswith("kanyoza") else name)
