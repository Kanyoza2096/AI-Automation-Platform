# core/redis_client.py
"""
Upstash Redis REST API client for Kanyoza Systems AI Platform v10.

Uses Upstash's HTTPS REST API instead of native Redis protocol.
This avoids adding redis-py as a dependency.

Credentials resolved from Settings (Pydantic v2).
Graceful fallback to in-memory storage when Redis is unreachable.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class RedisConfig:
    """Configuration for Upstash Redis REST API."""
    url: str
    token: str
    enabled: bool = True
    timeout: float = 5.0
    max_retries: int = 2


class RedisConnectionError(Exception):
    """Redis is unreachable — system will use in-memory fallback."""
    pass


class UpstashRedisClient:
    """
    Upstash Redis client using REST API.

    Features:
    - Automatic in-memory fallback when Redis is unreachable
    - Lazy health checking (cached is_available flag)
    - Thread-safe fallback operations
    - Graceful degradation — system works without Redis
    """

    def __init__(self, config: RedisConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        })
        self._in_memory_fallback: Dict[str, Any] = {}
        self._in_memory_expiry: Dict[str, float] = {}
        self._in_memory_lists: Dict[str, list] = {}
        self._lock = threading.Lock()
        self._available: Optional[bool] = None

        # Eagerly test connection on init
        if self.config.enabled:
            try:
                self.ping()
                self._available = True
                logger.info("Redis connected — %s", self._mask_url())
            except Exception as e:
                logger.warning("Redis unavailable (%s) — using in-memory fallback: %s", self._mask_url(), e)
                self._available = False

    def _mask_url(self) -> str:
        """Mask the URL for safe logging."""
        if not self.config.url:
            return "not-configured"
        parts = self.config.url.replace("https://", "").split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.***"
        return "***"

    # ------------------------------------------------------------------
    # HTTP Request Layer
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> dict:
        """Make a request to Upstash REST API with retry logic."""
        url = f"{self.config.url}{path}"

        for attempt in range(self.config.max_retries + 1):
            try:
                if method == "GET":
                    response = self._session.get(url, timeout=self.config.timeout)
                elif method == "POST":
                    response = self._session.post(url, json=json_data, timeout=self.config.timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 1))
                    time.sleep(retry_after)
                    continue

                logger.warning("Redis API error: HTTP %s — %s", response.status_code, response.text[:200])
                raise RedisConnectionError(f"HTTP {response.status_code}: {response.text[:200]}")

            except requests.RequestException as e:
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RedisConnectionError(f"Connection failed after {self.config.max_retries + 1} attempts: {e}")

        raise RedisConnectionError("Max retries exhausted")

    def _cmd(self, *args) -> dict:
        """
        Send an Upstash REST command using the POST JSON-array format.

        This is the safe way to pass values that may contain special characters
        (colons, plus signs, slashes, spaces, etc.) — e.g. ISO datetime strings,
        JSON blobs, or UUIDs.  The URL-path format (/set/key/value) breaks on
        any of those characters and returns ERR syntax error from Upstash.

        Upstash docs: POST / with body ["COMMAND", "arg1", "arg2", ...]
        """
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._session.post(
                    self.config.url,
                    json=list(args),
                    timeout=self.config.timeout,
                )
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 1))
                    time.sleep(retry_after)
                    continue
                logger.warning("Redis API error: HTTP %s — %s", response.status_code, response.text[:200])
                raise RedisConnectionError(f"HTTP {response.status_code}: {response.text[:200]}")
            except requests.RequestException as e:
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RedisConnectionError(f"Connection failed after {self.config.max_retries + 1} attempts: {e}")
        raise RedisConnectionError("Max retries exhausted")

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Check if Redis is reachable (cached)."""
        if not self.config.enabled:
            return False
        if self._available is None:
            try:
                self.ping()
                self._available = True
            except Exception:
                self._available = False
        return self._available

    def ping(self) -> bool:
        """PING the Redis server."""
        result = self._request("GET", "/ping")
        return result.get("result") == "PONG"

    def health_check(self) -> dict:
        """Health status for /api/v1/health/deep."""
        if not self.config.enabled:
            return {"redis": "disabled", "url": self._mask_url(), "fallback_active": True}

        try:
            start = time.time()
            is_alive = self.ping()
            latency_ms = round((time.time() - start) * 1000, 2)

            return {
                "redis": "healthy" if is_alive else "unhealthy",
                "latency_ms": latency_ms,
                "url": self._mask_url(),
                "fallback_active": not self._available,
            }
        except Exception as e:
            return {
                "redis": "degraded",
                "error": str(e)[:200],
                "url": self._mask_url(),
                "fallback_active": True,
            }

    # ------------------------------------------------------------------
    # String Operations
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        if not self.is_available:
            return self._fallback_get(key)
        try:
            result = self._request("GET", f"/get/{key}")
            value = result.get("result")
            return value if value != "null" else None
        except RedisConnectionError:
            self._available = False
            return self._fallback_get(key)

    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        if not self.is_available:
            self._fallback_set(key, value, ex)
            return True
        try:
            # Use _cmd() (POST JSON-array) so values with special characters
            # (colons, plus signs in ISO timestamps, slashes, etc.) don't cause
            # ERR syntax error when embedded in the URL path.
            args = ["SET", key, str(value)]
            if ex is not None:
                args += ["EX", ex]
            result = self._cmd(*args)
            return result.get("result") == "OK"
        except RedisConnectionError:
            self._available = False
            self._fallback_set(key, value, ex)
            return False

    def delete(self, key: str) -> bool:
        if not self.is_available:
            return self._fallback_delete(key)
        try:
            result = self._request("GET", f"/del/{key}")
            return result.get("result", 0) > 0
        except RedisConnectionError:
            self._available = False
            return self._fallback_delete(key)

    def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        return self.set(key, json.dumps(value, default=str), ex)

    def get_json(self, key: str) -> Optional[Any]:
        value = self.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    # ------------------------------------------------------------------
    # Atomic Operations
    # ------------------------------------------------------------------

    def incr(self, key: str) -> int:
        if not self.is_available:
            return self._fallback_incr(key)
        try:
            result = self._request("GET", f"/incr/{key}")
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._fallback_incr(key)

    def expire(self, key: str, seconds: int) -> bool:
        if not self.is_available:
            self._fallback_set_expiry(key, seconds)
            return True
        try:
            result = self._request("GET", f"/expire/{key}/{seconds}")
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            self._fallback_set_expiry(key, seconds)
            return False

    def setnx(self, key: str, value: str) -> bool:
        if not self.is_available:
            return self._fallback_setnx(key, value)
        try:
            result = self._cmd("SETNX", key, str(value))
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            return self._fallback_setnx(key, value)

    # ------------------------------------------------------------------
    # List Operations
    # ------------------------------------------------------------------

    def lpush(self, key: str, value: str) -> int:
        if not self.is_available:
            return self._fallback_lpush(key, value)
        try:
            result = self._cmd("LPUSH", key, str(value))
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._fallback_lpush(key, value)

    def rpop(self, key: str) -> Optional[str]:
        if not self.is_available:
            return self._fallback_rpop(key)
        try:
            result = self._request("GET", f"/rpop/{key}")
            value = result.get("result")
            return value if value != "null" else None
        except RedisConnectionError:
            self._available = False
            return self._fallback_rpop(key)

    # ------------------------------------------------------------------
    # In-Memory Fallback
    # ------------------------------------------------------------------

    def _fallback_get(self, key: str) -> Optional[str]:
        with self._lock:
            expiry = self._in_memory_expiry.get(key)
            if expiry and time.time() > expiry:
                self._in_memory_fallback.pop(key, None)
                self._in_memory_expiry.pop(key, None)
                return None
            return self._in_memory_fallback.get(key)

    def _fallback_set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        with self._lock:
            self._in_memory_fallback[key] = value
            if ex is not None:
                self._in_memory_expiry[key] = time.time() + ex

    def _fallback_delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._in_memory_fallback
            self._in_memory_fallback.pop(key, None)
            self._in_memory_expiry.pop(key, None)
            return existed

    def _fallback_incr(self, key: str) -> int:
        with self._lock:
            current = int(self._in_memory_fallback.get(key, 0))
            current += 1
            self._in_memory_fallback[key] = str(current)
            return current

    def _fallback_set_expiry(self, key: str, seconds: int) -> None:
        with self._lock:
            if key in self._in_memory_fallback:
                self._in_memory_expiry[key] = time.time() + seconds

    def _fallback_setnx(self, key: str, value: str) -> bool:
        with self._lock:
            if key not in self._in_memory_fallback:
                self._in_memory_fallback[key] = value
                return True
            return False

    def _fallback_lpush(self, key: str, value: str) -> int:
        list_key = f"_list:{key}"
        with self._lock:
            if list_key not in self._in_memory_lists:
                self._in_memory_lists[list_key] = []
            self._in_memory_lists[list_key].insert(0, value)
            return len(self._in_memory_lists[list_key])

    def _fallback_rpop(self, key: str) -> Optional[str]:
        list_key = f"_list:{key}"
        with self._lock:
            if list_key in self._in_memory_lists and self._in_memory_lists[list_key]:
                return self._in_memory_lists[list_key].pop()
            return None
