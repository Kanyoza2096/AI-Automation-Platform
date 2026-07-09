"""
core/log_buffer.py — Thread-safe in-memory ring buffer for structured log entries.

Singleton. Stores the last 500 log records and supports SSE push via subscriber callbacks.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, List, Optional


class LogEntry:
    __slots__ = ("timestamp", "level", "module", "message", "correlation_id")

    def __init__(
        self,
        level: str,
        module: str,
        message: str,
        correlation_id: str = "",
    ) -> None:
        self.timestamp = time.time()
        self.level = level.upper()
        self.module = module
        self.message = message
        self.correlation_id = correlation_id

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "module": self.module,
            "message": self.message,
            "correlation_id": self.correlation_id,
        }


class LogBuffer:
    """
    Singleton in-memory ring buffer that captures structured log entries.

    Usage
    -----
    buf = LogBuffer.get_instance()
    buf.add("INFO", "kanyoza.api", "Request received", correlation_id="abc")
    recent = buf.get_recent(limit=50, level="ERROR")
    """

    _instance: Optional["LogBuffer"] = None
    _lock: threading.Lock = threading.Lock()

    MAX_ENTRIES = 500

    def __init__(self) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=self.MAX_ENTRIES)
        self._mutex = threading.Lock()
        self._subscribers: List[Callable[[LogEntry], None]] = []
        self._sub_lock = threading.Lock()
        self._start_time = time.time()

        # Counters
        self._total = 0
        self._errors = 0
        self._warnings = 0
        self._info = 0

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "LogBuffer":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        level: str,
        module: str,
        message: str,
        correlation_id: str = "",
    ) -> None:
        entry = LogEntry(level, module, message, correlation_id)

        with self._mutex:
            self._entries.append(entry)
            self._total += 1
            lvl = entry.level
            if lvl == "ERROR" or lvl == "CRITICAL":
                self._errors += 1
            elif lvl == "WARNING":
                self._warnings += 1
            elif lvl == "INFO":
                self._info += 1

        # Push to subscribers outside the main lock
        self._notify(entry)

    def clear(self) -> None:
        with self._mutex:
            self._entries.clear()
            self._total = 0
            self._errors = 0
            self._warnings = 0
            self._info = 0

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_recent(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        module: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[dict]:
        with self._mutex:
            entries = list(self._entries)

        # Filter
        if level:
            lvl_upper = level.upper()
            entries = [e for e in entries if e.level == lvl_upper]
        if module:
            mod_lower = module.lower()
            entries = [e for e in entries if mod_lower in e.module.lower()]
        if search:
            s = search.lower()
            entries = [e for e in entries if s in e.message.lower() or s in e.module.lower()]

        # Most recent first, limited
        entries = entries[-limit:]
        entries.reverse()
        return [e.to_dict() for e in entries]

    def get_stats(self) -> dict:
        with self._mutex:
            total = self._total
            errors = self._errors
            warnings = self._warnings
            info = self._info
            buffered = len(self._entries)

        return {
            "total_logs": total,
            "buffered": buffered,
            "errors": errors,
            "warnings": warnings,
            "info": info,
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }

    # ------------------------------------------------------------------
    # SSE subscribers
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[LogEntry], None]) -> None:
        with self._sub_lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[LogEntry], None]) -> None:
        with self._sub_lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def _notify(self, entry: LogEntry) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(entry)
            except Exception:
                pass
