"""
Structured Logger v1.0 — JSON logging with trace IDs
- Request ID tracking across all services
- Performance timing built-in
- Machine-parseable output
"""

import logging
import json
import time
import uuid
from datetime import datetime
from functools import wraps


class StructuredLogger:
    """JSON-structured logger with trace support."""

    def __init__(self, name: str = "kanyoza"):
        self.logger = logging.getLogger(name)
        self._request_id = None

    @property
    def request_id(self):
        if not self._request_id:
            self._request_id = str(uuid.uuid4())[:8]
        return self._request_id

    def set_request_id(self, request_id: str):
        self._request_id = request_id

    def _log(self, level: str, message: str, **kwargs):
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "request_id": self.request_id,
            "message": message,
            **kwargs,
        }
        getattr(self.logger, level)(json.dumps(log_data))

    def info(self, message: str, **kwargs):
        self._log("info", message, **kwargs)

    def warning(self, message: str, **kwargs):
        self._log("warning", message, **kwargs)

    def error(self, message: str, **kwargs):
        self._log("error", message, **kwargs)

    def debug(self, message: str, **kwargs):
        self._log("debug", message, **kwargs)

    def critical(self, message: str, **kwargs):
        self._log("critical", message, **kwargs)

    def timed(self, func):
        """Decorator to log function execution time."""
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration = (time.time() - start) * 1000
                self.info(f"{func.__name__} completed", duration_ms=round(duration, 2))
                return result
            except Exception as e:
                duration = (time.time() - start) * 1000
                self.error(f"{func.__name__} failed", duration_ms=round(duration, 2), error=str(e))
                raise
        return wrapper


logger = StructuredLogger()
