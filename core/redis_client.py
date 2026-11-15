"""
Kanyoza Redis Client v11 — Upstash REST + Traditional Redis Support.

Supports:
1. Upstash Redis REST API (serverless)
2. Traditional Redis (redis://host:port with password)
3. Graceful in-memory fallback

Credentials resolved from Settings.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class RedisConfig:
    """Configuration for Redis."""
    url: str = ""           # Upstash REST URL or traditional redis:// URL
    token: str = ""         # Upstash token or Redis password
    enabled: bool = True
    timeout: float = 5.0
    max_retries: int = 2
    mode: str = "auto"      # "upstash", "traditional", or "auto"


class RedisConnectionError(Exception):
    """Redis is unreachable — system will use in-memory fallback."""
    pass


class UpstashRedisClient:
    """
    Redis client supporting BOTH Upstash REST and traditional Redis.

    Auto-detects mode based on URL format:
    - https://*.upstash.io → Upstash REST
    - redis://host:port → Traditional Redis
    """

    def __init__(self, config: RedisConfig) -> None:
        self.config = config
        
        # Detect mode
        if config.mode == "auto":
            self.mode = self._detect_mode(config.url)
        else:
            self.mode = config.mode
        
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
        })
        
        self._redis_client = None  # For traditional Redis
        
        self._in_memory_fallback: Dict[str, Any] = {}
        self._in_memory_expiry: Dict[str, float] = {}
        self._in_memory_lists: Dict[str, list] = {}
        self._lock = threading.Lock()
        self._available: Optional[bool] = None
        
        # Request budget for Upstash free tier
        self._request_budget = int(os.getenv("UPSTASH_MAX_REQUESTS", "500000"))
        self._requests_used = 0
        
        if self.config.enabled:
            self._initialize()
    
    def _detect_mode(self, url: str) -> str:
        """Detect Redis mode from URL."""
        if not url:
            return "fallback"
        if "upstash.io" in url:
            return "upstash"
        if url.startswith("redis://") or url.startswith("rediss://"):
            return "traditional"
        return "fallback"
    
    def _initialize(self) -> None:
        """Initialize connection."""
        try:
            if self.mode == "traditional":
                self._init_traditional_redis()
            elif self.mode == "upstash":
                self._session.headers.update({
                    "Authorization": f"Bearer {self.config.token}",
                })
                self.ping()
                self._available = True
            else:
                self._available = False
                logger.info("Redis not configured — using in-memory fallback")
        except Exception as e:
            logger.warning("Redis unavailable (%s) — using in-memory fallback: %s", self._mask_url(), e)
            self._available = False
    
    def _init_traditional_redis(self) -> None:
        """Initialize traditional Redis connection."""
        try:
            import redis as redis_lib
            
            redis_url = self.config.url
            
            # Add password if provided
            if self.config.token and "://" in redis_url:
                # Insert password into URL
                scheme, rest = redis_url.split("://", 1)
                redis_url = f"{scheme}://:{self.config.token}@{rest}"
            
            self._redis_client = redis_lib.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=self.config.timeout,
            )
            
            # Test connection
            self._redis_client.ping()
            self._available = True
            logger.info("Traditional Redis connected — %s", self._mask_url())
        except ImportError:
            logger.warning("redis-py not installed — falling back to in-memory")
            self._available = False
        except Exception as e:
            logger.warning("Traditional Redis connection failed: %s", e)
            self._available = False
    
    def _mask_url(self) -> str:
        """Mask URL for safe logging."""
        if not self.config.url:
            return "not-configured"
        # Mask password
        if "://" in self.config.url:
            parts = self.config.url.split("://")
            scheme = parts[0]
            rest = parts[1]
            if "@" in rest:
                rest = "***@" + rest.split("@")[-1]
            return f"{scheme}://{rest}"
        parts = self.config.url.replace("https://", "").split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.***"
        return "***"

    # ------------------------------------------------------------------
    # Request Budget (Upstash free tier protection)
    # ------------------------------------------------------------------

    def _check_budget(self) -> bool:
        """Check if we're within Upstash request budget."""
        if self.mode == "upstash":
            if self._requests_used >= self._request_budget * 0.95:
                logger.warning("Upstash request budget nearly exhausted (%d/%d) — falling back to memory", 
                               self._requests_used, self._request_budget)
                self._available = False
                return False
        return True

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Check if Redis is reachable."""
        if not self.config.enabled:
            return False
        if self.mode == "fallback":
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
        if self.mode == "traditional" and self._redis_client:
            try:
                return self._redis_client.ping()
            except Exception:
                self._available = False
                raise RedisConnectionError("Traditional Redis ping failed")
        
        if self.mode == "upstash":
            result = self._request("GET", "/ping")
            return result.get("result") == "PONG"
        
        return False

    def health_check(self) -> dict:
        """Health status."""
        if not self.config.enabled:
            return {"redis": "disabled", "mode": "fallback", "fallback_active": True}
        
        try:
            start = time.time()
            is_alive = self.ping()
            latency_ms = round((time.time() - start) * 1000, 2)
            
            return {
                "redis": "healthy" if is_alive else "unhealthy",
                "mode": self.mode,
                "latency_ms": latency_ms,
                "url": self._mask_url(),
                "fallback_active": not self._available,
            }
        except Exception as e:
            return {
                "redis": "degraded",
                "mode": self.mode,
                "error": str(e)[:200],
                "url": self._mask_url(),
                "fallback_active": True,
            }

    # ------------------------------------------------------------------
    # String Operations
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        if not self.is_available or not self._check_budget():
            return self._fallback_get(key)
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                value = self._redis_client.get(key)
                return value
            except Exception:
                self._available = False
                return self._fallback_get(key)
        
        try:
            self._requests_used += 1
            result = self._request("GET", f"/get/{key}")
            value = result.get("result")
            return value if value != "null" else None
        except RedisConnectionError:
            self._available = False
            return self._fallback_get(key)

    def inspect_distributed_lock(self, key: str) -> dict:
        """Read lock existence and TTL atomically, without exposing its owner token.

        This is a strict Redis-only diagnostic: it never reads the in-memory
        fallback and never mutates or deletes the lock.
        """
        if not self.is_available:
            raise RedisConnectionError("Redis unavailable; lock inspection refused")
        if not self._check_budget():
            raise RedisConnectionError("Redis request budget exhausted; lock inspection refused")

        script = (
            "local v = redis.call('GET', KEYS[1]); "
            "local ttl = redis.call('TTL', KEYS[1]); "
            "return {v and 1 or 0, ttl}"
        )
        try:
            self._requests_used += 1
            if self.mode == "traditional" and self._redis_client:
                result = self._redis_client.eval(script, 1, key)
            elif self.mode == "upstash":
                response = self._cmd("EVAL", script, "1", key)
                result = response.get("result")
            else:
                raise RedisConnectionError("Redis transport is not configured")
            if not isinstance(result, (list, tuple)) or len(result) != 2:
                raise RedisConnectionError("Unexpected Redis lock inspection response")
            return {"exists": bool(int(result[0])), "ttl_seconds": int(result[1])}
        except RedisConnectionError:
            self._available = False
            raise
        except Exception as exc:
            self._available = False
            raise RedisConnectionError(
                f"Redis lock inspection failed ({type(exc).__name__})"
            ) from exc

    def set_distributed(self, key: str, value: str, ex: Optional[int] = None, *, nx: bool = False) -> bool:
        """Execute SET against Redis only; never use the in-memory fallback.

        Intended for distributed locks and other coordination primitives where
        a local fallback would create a false claim of cross-process ownership.
        """
        if not self.is_available:
            raise RedisConnectionError("Redis unavailable; distributed SET refused")
        if not self._check_budget():
            raise RedisConnectionError("Redis request budget exhausted; distributed SET refused")

        try:
            self._requests_used += 1
            if self.mode == "traditional" and self._redis_client:
                result = self._redis_client.set(key, str(value), ex=ex, nx=nx)
                return bool(result)

            if self.mode == "upstash":
                args = ["SET", key, str(value)]
                if nx:
                    args.append("NX")
                if ex is not None:
                    args += ["EX", ex]
                result = self._cmd(*args)
                return result.get("result") == "OK"

            raise RedisConnectionError("Redis transport is not configured")
        except RedisConnectionError:
            self._available = False
            raise
        except Exception as exc:
            self._available = False
            raise RedisConnectionError(f"Distributed SET failed: {exc}") from exc

    def set(self, key: str, value: str, ex: Optional[int] = None, *, nx: bool = False) -> bool:
        if not self.is_available or not self._check_budget():
            return self._fallback_set(key, value, ex, nx=nx)

        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                result = self._redis_client.set(key, str(value), ex=ex, nx=nx)
                return bool(result)
            except Exception:
                self._available = False
                return self._fallback_set(key, value, ex, nx=nx)

        try:
            self._requests_used += 1
            args = ["SET", key, str(value)]
            if nx:
                args.append("NX")
            if ex is not None:
                args += ["EX", ex]
            result = self._cmd(*args)
            return result.get("result") == "OK"
        except RedisConnectionError:
            self._available = False
            return self._fallback_set(key, value, ex, nx=nx)

    def delete(self, key: str) -> bool:
        if not self.is_available:
            return self._fallback_delete(key)
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return self._redis_client.delete(key) > 0
            except Exception:
                self._available = False
                return self._fallback_delete(key)
        
        try:
            self._requests_used += 1
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
        if not self.is_available or not self._check_budget():
            return self._fallback_incr(key)
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return self._redis_client.incr(key)
            except Exception:
                self._available = False
                return self._fallback_incr(key)
        
        try:
            self._requests_used += 1
            result = self._request("GET", f"/incr/{key}")
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._fallback_incr(key)

    def expire(self, key: str, seconds: int) -> bool:
        if not self.is_available:
            self._fallback_set_expiry(key, seconds)
            return True
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return self._redis_client.expire(key, seconds)
            except Exception:
                self._available = False
                return False
        
        try:
            self._requests_used += 1
            result = self._request("GET", f"/expire/{key}/{seconds}")
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            self._fallback_set_expiry(key, seconds)
            return False

    def setnx(self, key: str, value: str) -> bool:
        if not self.is_available:
            return self._fallback_setnx(key, value)
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return self._redis_client.setnx(key, str(value))
            except Exception:
                self._available = False
                return self._fallback_setnx(key, value)
        
        try:
            self._requests_used += 1
            result = self._cmd("SETNX", key, str(value))
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            return self._fallback_setnx(key, value)

    def compare_and_delete(self, key: str, expected_value: str) -> bool:
        """Delete a key only when its value still matches the owner token."""
        if not expected_value:
            return False
        if not self.is_available:
            with self._lock:
                expiry = self._in_memory_expiry.get(key)
                if expiry and time.time() > expiry:
                    self._in_memory_fallback.pop(key, None)
                    self._in_memory_expiry.pop(key, None)
                current = self._in_memory_fallback.get(key)
                if current != expected_value:
                    return False
                self._in_memory_fallback.pop(key, None)
                self._in_memory_expiry.pop(key, None)
                return True
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                script = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"
                return int(self._redis_client.eval(script, 1, key, expected_value)) == 1
            except Exception:
                self._available = False
                return False
        try:
            self._requests_used += 1
            script = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"
            result = self._cmd("EVAL", script, "1", key, expected_value)
            return int(result.get("result", 0)) == 1
        except RedisConnectionError:
            self._available = False
            return False

    def compare_and_expire(self, key: str, expected_value: str, seconds: int) -> bool:
        """Extend a lease only while the caller still owns its token."""
        if seconds <= 0 or not expected_value:
            raise ValueError("seconds must be positive and expected_value is required")
        if not self.is_available:
            return False
        script = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) else return 0 end"
        try:
            self._requests_used += 1
            if self.mode == "traditional" and self._redis_client:
                return int(self._redis_client.eval(script, 1, key, expected_value, str(seconds))) == 1
            result = self._cmd("EVAL", script, "1", key, expected_value, str(seconds))
            return int(result.get("result", 0)) == 1
        except Exception:
            self._available = False
            return False

    # ------------------------------------------------------------------
    # List Operations
    # ------------------------------------------------------------------

    def lpush(self, key: str, value: str) -> int:
        if not self.is_available:
            return self._fallback_lpush(key, value)
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return self._redis_client.lpush(key, str(value))
            except Exception:
                self._available = False
                return self._fallback_lpush(key, value)
        
        try:
            self._requests_used += 1
            result = self._cmd("LPUSH", key, str(value))
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._fallback_lpush(key, value)

    def lrange(self, key: str, start: int = 0, stop: int = -1):
        if not self.is_available:
            return self._fallback_lrange(key, start, stop)
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return list(self._redis_client.lrange(key, start, stop))
            except Exception:
                self._available = False
                return self._fallback_lrange(key, start, stop)
        try:
            self._requests_used += 1
            result = self._cmd("LRANGE", key, start, stop)
            return result.get("result") or []
        except RedisConnectionError:
            self._available = False
            return self._fallback_lrange(key, start, stop)

    def lrem(self, key: str, count: int, value: str) -> int:
        if not self.is_available:
            return self._fallback_lrem(key, count, value)
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return int(self._redis_client.lrem(key, count, value))
            except Exception:
                self._available = False
                return self._fallback_lrem(key, count, value)
        try:
            self._requests_used += 1
            result = self._cmd("LREM", key, count, value)
            return int(result.get("result") or 0)
        except RedisConnectionError:
            self._available = False
            return self._fallback_lrem(key, count, value)

    def rpop(self, key: str) -> Optional[str]:
        if not self.is_available:
            return self._fallback_rpop(key)
        
        if self.mode == "traditional" and self._redis_client:
            try:
                self._requests_used += 1
                return self._redis_client.rpop(key)
            except Exception:
                self._available = False
                return self._fallback_rpop(key)
        
        try:
            self._requests_used += 1
            result = self._request("GET", f"/rpop/{key}")
            value = result.get("result")
            return value if value != "null" else None
        except RedisConnectionError:
            self._available = False
            return self._fallback_rpop(key)

    # ------------------------------------------------------------------
    # HTTP Request Layer (Upstash only)
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> dict:
        """Make a request to Upstash REST API."""
        url = f"{self.config.url}{path}"
        
        for attempt in range(self.config.max_retries + 1):
            try:
                if method == "GET":
                    response = self._session.get(url, timeout=self.config.timeout)
                elif method == "POST":
                    response = self._session.post(url, json=json_data, timeout=self.config.timeout)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                if response.status_code == 200:
                    return response.json()
                
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 1))
                    time.sleep(retry_after)
                    continue
                
                logger.warning("Redis API error: HTTP %s — %s", response.status_code, response.text[:200])
                raise RedisConnectionError(f"HTTP {response.status_code}")
                
            except requests.RequestException as e:
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RedisConnectionError(f"Connection failed: {e}")
        
        raise RedisConnectionError("Max retries exhausted")

    def _cmd(self, *args) -> dict:
        """Send Upstash REST command."""
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
                    time.sleep(int(response.headers.get("Retry-After", 1)))
                    continue
                raise RedisConnectionError(f"HTTP {response.status_code}")
            except requests.RequestException as e:
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RedisConnectionError(f"Connection failed: {e}")
        raise RedisConnectionError("Max retries exhausted")

    # ------------------------------------------------------------------
    # In-Memory Fallback (unchanged)
    # ------------------------------------------------------------------

    def _fallback_get(self, key: str) -> Optional[str]:
        with self._lock:
            expiry = self._in_memory_expiry.get(key)
            if expiry and time.time() > expiry:
                self._in_memory_fallback.pop(key, None)
                self._in_memory_expiry.pop(key, None)
                return None
            return self._in_memory_fallback.get(key)

    def _fallback_set(self, key: str, value: str, ex: Optional[int] = None, *, nx: bool = False) -> bool:
        with self._lock:
            if key in self._in_memory_expiry and time.time() > self._in_memory_expiry[key]:
                self._in_memory_fallback.pop(key, None)
                self._in_memory_expiry.pop(key, None)
            if nx and key in self._in_memory_fallback:
                return False
            self._in_memory_fallback[key] = str(value)
            if ex is not None:
                self._in_memory_expiry[key] = time.time() + ex
            else:
                self._in_memory_expiry.pop(key, None)
            return True

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

    def _fallback_lrange(self, key: str, start: int = 0, stop: int = -1):
        with self._lock:
            values = list(self._in_memory_lists.get(f"_list:{key}", []))
        if stop == -1:
            stop = len(values) - 1
        return values[start:stop + 1] if values else []

    def _fallback_lrem(self, key: str, count: int, value: str) -> int:
        with self._lock:
            values = self._in_memory_lists.get(f"_list:{key}", [])
            removed = 0
            if count == 0:
                new = [v for v in values if not (v == value and not (removed := removed + 1))]
            else:
                new = []
                remaining = abs(count)
                seq = values if count > 0 else list(reversed(values))
                for v in seq:
                    if v == value and remaining > 0:
                        removed += 1
                        remaining -= 1
                    else:
                        new.append(v)
                if count < 0:
                    new.reverse()
            self._in_memory_lists[f"_list:{key}"] = new
            return removed

    def _fallback_rpop(self, key: str) -> Optional[str]:
        list_key = f"_list:{key}"
        with self._lock:
            if list_key in self._in_memory_lists and self._in_memory_lists[list_key]:
                return self._in_memory_lists[list_key].pop()
            return None
