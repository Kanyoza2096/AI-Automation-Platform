from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional


class ReplayGuard:
    """Prevents webhook replay attacks by tracking recently seen message IDs.

    Keeps an in-memory set with TTL-based expiration. For a multi-process
    deployment, swap the backing store for Redis or Supabase.
    """

    def __init__(self, ttl_seconds: int = 300, max_size: int = 10_000) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, ts in self._seen.items() if now - ts > self._ttl]
        for k in expired:
            del self._seen[k]

    def is_replay(self, message_id: str) -> bool:
        with self._lock:
            self._evict_expired()
            if message_id in self._seen:
                return True
            if len(self._seen) >= self._max_size:
                oldest = min(self._seen, key=self._seen.get)
                del self._seen[oldest]
            self._seen[message_id] = time.monotonic()
            return False

    def fingerprint(self, sender_id: str, text: str, timestamp: Optional[int] = None) -> str:
        raw = f"{sender_id}:{text}:{timestamp or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def stats(self) -> dict:
        with self._lock:
            return {"tracked": len(self._seen), "ttl_s": self._ttl}
