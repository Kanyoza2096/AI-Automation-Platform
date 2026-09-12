"""
Distributed coordination lock — honest modes (architecture report §21).

Redis / DB distributed coordination
        │
        ▼
persistent ownership

If Redis is unavailable:
  mode = DEGRADED_LOCAL
  → single-process only; callers that require multi-instance safety
    must refuse autonomous publish / critical jobs.

Never pretend an in-process lock is a distributed lock.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("kanyoza.core.distributed_lock")


class LockMode(str, Enum):
    DISTRIBUTED = "distributed"       # Redis held
    DEGRADED_LOCAL = "degraded_local" # process-local only
    UNAVAILABLE = "unavailable"       # could not acquire


class DistributedLock:
    """
    Acquire semantics:
      - Prefer Redis SET NX EX
      - If Redis missing/fails: fall back to thread lock and mark DEGRADED_LOCAL
      - acquire() returns True/False; check .mode after acquire
      - require_distributed=True → do not fall back (return False, mode=UNAVAILABLE)
    """

    def __init__(
        self,
        name: str,
        redis_client: Any = None,
        timeout_seconds: float = 300.0,
        *,
        allow_local_fallback: bool = True,
    ):
        self.name = name
        self.redis = redis_client
        self.timeout_seconds = timeout_seconds
        self.allow_local_fallback = allow_local_fallback
        self._thread_lock = threading.Lock()
        self._lock_holder: Optional[str] = None
        self._lock_expires: float = 0.0
        self.mode: LockMode = LockMode.UNAVAILABLE
        self._redis_healthy: Optional[bool] = None

    @property
    def is_distributed(self) -> bool:
        return self.mode == LockMode.DISTRIBUTED

    @property
    def is_degraded_local(self) -> bool:
        return self.mode == LockMode.DEGRADED_LOCAL

    def acquire(self, *, require_distributed: bool = False) -> bool:
        """
        Try to acquire. Sets self.mode.

        require_distributed=True: only succeed with Redis; no local fallback.
        """
        # Redis path. The client must be genuinely reachable; its convenience
        # fallback must never be mistaken for distributed ownership.
        if self.redis is not None:
            try:
                if hasattr(self.redis, "is_available") and not self.redis.is_available:
                    raise RuntimeError("Redis client is unavailable")
                token = uuid.uuid4().hex
                result = self.redis.set(
                    f"lock:{self.name}",
                    token,
                    ex=int(self.timeout_seconds),
                    nx=True,
                )
                if result:
                    self._lock_holder = token
                    self._lock_expires = time.monotonic() + self.timeout_seconds
                    self.mode = LockMode.DISTRIBUTED
                    self._redis_healthy = True
                    return True
                # someone else holds it
                self.mode = LockMode.UNAVAILABLE
                self._redis_healthy = True
                return False
            except Exception as exc:
                self._redis_healthy = False
                logger.warning(
                    "DistributedLock '%s': Redis error (%s) — %s",
                    self.name,
                    exc,
                    "no local fallback" if require_distributed or not self.allow_local_fallback else "DEGRADED_LOCAL",
                )
                if require_distributed or not self.allow_local_fallback:
                    self.mode = LockMode.UNAVAILABLE
                    return False
        else:
            if require_distributed or not self.allow_local_fallback:
                self.mode = LockMode.UNAVAILABLE
                logger.warning(
                    "DistributedLock '%s': no Redis — refusing (require_distributed/no fallback)",
                    self.name,
                )
                return False

        # Local fallback (single process only)
        if self._thread_lock.acquire(blocking=False):
            self._lock_holder = "thread"
            self._lock_expires = time.monotonic() + self.timeout_seconds
            self.mode = LockMode.DEGRADED_LOCAL
            logger.debug(
                "DistributedLock '%s': acquired DEGRADED_LOCAL (not safe across instances)",
                self.name,
            )
            return True
        self.mode = LockMode.UNAVAILABLE
        return False

    def renew(self, timeout_seconds: Optional[float] = None) -> bool:
        """Renew a distributed lease without allowing a stale owner to extend it."""
        if not self.is_distributed or not self._lock_holder or self.redis is None:
            return False
        seconds = int(timeout_seconds if timeout_seconds is not None else self.timeout_seconds)
        if seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        try:
            ok = self.redis.compare_and_expire(f"lock:{self.name}", self._lock_holder, seconds)
            if ok:
                self._lock_expires = time.monotonic() + seconds
            else:
                self.mode = LockMode.UNAVAILABLE
            return bool(ok)
        except Exception as exc:
            logger.warning("DistributedLock '%s': lease renewal failed: %s", self.name, exc)
            self.mode = LockMode.UNAVAILABLE
            return False

    def release(self) -> None:
        if self.redis is not None and self._lock_holder and self._lock_holder != "thread":
            try:
                if hasattr(self.redis, "compare_and_delete"):
                    self.redis.compare_and_delete(f"lock:{self.name}", self._lock_holder)
                else:
                    current = self.redis.get(f"lock:{self.name}")
                    if current is not None:
                        val = current.decode() if isinstance(current, (bytes, bytearray)) else str(current)
                        if val == self._lock_holder:
                            self.redis.delete(f"lock:{self.name}")
            except Exception as exc:
                logger.debug("DistributedLock release Redis error: %s", exc)
        elif self._lock_holder == "thread":
            try:
                self._thread_lock.release()
            except RuntimeError:
                pass

        self._lock_holder = None
        self._lock_expires = 0.0
        self.mode = LockMode.UNAVAILABLE

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self._lock_expires

    def __enter__(self) -> "DistributedLock":
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock '{self.name}' mode={self.mode.value}")
        return self

    def __exit__(self, *args) -> None:
        self.release()
