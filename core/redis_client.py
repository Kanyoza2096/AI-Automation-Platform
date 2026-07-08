# core/redis_client.py
import json
import time
import threading
from typing import Optional, Any, Dict
import requests
from dataclasses import dataclass

@dataclass
class RedisConfig:
    """Configuration for Upstash Redis REST API"""
    url: str
    token: str
    enabled: bool = True
    timeout: float = 5.0
    max_retries: int = 2

class UpstashRedisClient:
    """
    Upstash Redis client using REST API.
    
    Upstash provides both:
    - Native Redis protocol (redis://) — requires `redis-py`
    - REST API (https://) — works with plain HTTP requests
    
    We use REST API to avoid adding another dependency.
    """
    
    def __init__(self, config: RedisConfig):
        self.config = config
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {config.token}",
            "Content-Type": "application/json",
        })
        self._in_memory_fallback: Dict[str, Any] = {}
        self._in_memory_expiry: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._available = None  # Lazily checked
    
    def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> dict:
        """Make a request to Upstash REST API"""
        url = f"{self.config.url}{path}"
        
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    json=json_data,
                    timeout=self.config.timeout
                )
                
                if response.status_code == 200:
                    return response.json()
                
                # Rate limited — wait and retry
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 1))
                    time.sleep(retry_after)
                    continue
                
                # Other error
                logger.warning(f"Redis API error: {response.status_code} - {response.text}")
                raise RedisConnectionError(f"HTTP {response.status_code}: {response.text}")
                
            except requests.RequestException as e:
                if attempt < self.config.max_retries:
                    time.sleep(2 ** attempt)
                    continue
                raise RedisConnectionError(f"Connection failed after {self.config.max_retries} retries: {e}")
        
        raise RedisConnectionError("Max retries exceeded")
    
    @property
    def is_available(self) -> bool:
        """Check if Redis is reachable — cached to avoid hammering the API"""
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
        """Test connection to Redis"""
        result = self._request("GET", "/ping")
        return result.get("result") == "PONG"
    
    # ============================================================
    # String Operations (Cache, Sessions, Rate Limiting)
    # ============================================================
    
    def get(self, key: str) -> Optional[str]:
        """GET a value — with automatic in-memory fallback"""
        if not self.is_available:
            return self._memory_fallback_get(key)
        
        try:
            result = self._request("GET", f"/get/{key}")
            return result.get("result")
        except RedisConnectionError:
            logger.warning(f"Redis unavailable for GET {key}, using fallback")
            self._available = False
            return self._memory_fallback_get(key)
    
    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """SET a value with optional expiry in seconds"""
        if not self.is_available:
            self._memory_fallback_set(key, value, ex)
            return True
        
        try:
            path = f"/set/{key}/{value}"
            if ex:
                path = f"/set/{key}/{value}/ex/{ex}"
            result = self._request("GET", path)
            return result.get("result") == "OK"
        except RedisConnectionError:
            self._available = False
            self._memory_fallback_set(key, value, ex)
            return False
    
    def delete(self, key: str) -> bool:
        """DELETE a key"""
        if not self.is_available:
            self._memory_fallback.pop(key, None)
            self._memory_expiry.pop(key, None)
            return True
        
        try:
            result = self._request("GET", f"/del/{key}")
            return result.get("result", 0) > 0
        except RedisConnectionError:
            self._available = False
            self._memory_fallback.pop(key, None)
            return False
    
    def exists(self, key: str) -> bool:
        """Check if key exists"""
        if not self.is_available:
            return key in self._memory_fallback
        
        try:
            result = self._request("GET", f"/exists/{key}")
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            return key in self._memory_fallback
    
    def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """SET a JSON-serializable value"""
        return self.set(key, json.dumps(value, default=str), ex)
    
    def get_json(self, key: str) -> Optional[Any]:
        """GET and deserialize a JSON value"""
        value = self.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    
    # ============================================================
    # Atomic Operations (Rate Limiting, Counters)
    # ============================================================
    
    def incr(self, key: str) -> int:
        """INCR — atomic increment, perfect for rate limiting"""
        if not self.is_available:
            return self._memory_fallback_incr(key)
        
        try:
            result = self._request("GET", f"/incr/{key}")
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._memory_fallback_incr(key)
    
    def incrby(self, key: str, amount: int) -> int:
        """INCRBY — increment by specific amount"""
        if not self.is_available:
            return self._memory_fallback_incrby(key, amount)
        
        try:
            result = self._request("GET", f"/incrby/{key}/{amount}")
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._memory_fallback_incrby(key, amount)
    
    def expire(self, key: str, seconds: int) -> bool:
        """EXPIRE — set TTL on a key"""
        if not self.is_available:
            self._memory_expiry[key] = time.time() + seconds
            return True
        
        try:
            result = self._request("GET", f"/expire/{key}/{seconds}")
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            self._memory_expiry[key] = time.time() + seconds
            return False
    
    def setnx(self, key: str, value: str) -> bool:
        """SETNX — set only if not exists (perfect for idempotency)"""
        if not self.is_available:
            if key not in self._memory_fallback:
                self._memory_fallback[key] = value
                return True
            return False
        
        try:
            path = f"/setnx/{key}/{value}"
            result = self._request("GET", path)
            return result.get("result", 0) == 1
        except RedisConnectionError:
            self._available = False
            if key not in self._memory_fallback:
                self._memory_fallback[key] = value
                return True
            return False
    
    # ============================================================
    # List Operations (Task Queue)
    # ============================================================
    
    def lpush(self, key: str, value: str) -> int:
        """LPUSH — push to left of list (task queue)"""
        if not self.is_available:
            return self._memory_fallback_list_push(key, value, left=True)
        
        try:
            result = self._request("GET", f"/lpush/{key}/{value}")
            return result.get("result", 0)
        except RedisConnectionError:
            self._available = False
            return self._memory_fallback_list_push(key, value, left=True)
    
    def rpop(self, key: str) -> Optional[str]:
        """RPOP — pop from right of list (FIFO queue)"""
        if not self.is_available:
            return self._memory_fallback_list_pop(key)
        
        try:
            result = self._request("GET", f"/rpop/{key}")
            return result.get("result")
        except RedisConnectionError:
            self._available = False
            return self._memory_fallback_list_pop(key)
    
    def llen(self, key: str) -> int:
        """LLEN — length of list (queue depth)"""
        if not self.is_available:
            return len(self._memory_fallback.get(f"_list:{key}", []))
        
        try:
            result = self._request("GET", f"/llen/{key}")
            return result.get("result", 0)
        except RedisConnectionError:
            return len(self._memory_fallback.get(f"_list:{key}", []))
    
    # ============================================================
    # In-Memory Fallback (Graceful Degradation)
    # ============================================================
    
    def _memory_fallback_get(self, key: str) -> Optional[str]:
        """In-memory GET with expiry check"""
        with self._lock:
            expiry = self._memory_expiry.get(key)
            if expiry and time.time() > expiry:
                self._memory_fallback.pop(key, None)
                self._memory_expiry.pop(key, None)
                return None
            return self._memory_fallback.get(key)
    
    def _memory_fallback_set(self, key: str, value: str, ex: Optional[int] = None):
        """In-memory SET with optional expiry"""
        with self._lock:
            self._memory_fallback[key] = value
            if ex:
                self._memory_expiry[key] = time.time() + ex
    
    def _memory_fallback_incr(self, key: str) -> int:
        """In-memory INCR"""
        with self._lock:
            current = int(self._memory_fallback.get(key, 0))
            current += 1
            self._memory_fallback[key] = str(current)
            return current
    
    def _memory_fallback_incrby(self, key: str, amount: int) -> int:
        """In-memory INCRBY"""
        with self._lock:
            current = int(self._memory_fallback.get(key, 0))
            current += amount
            self._memory_fallback[key] = str(current)
            return current
    
    def _memory_fallback_list_push(self, key: str, value: str, left: bool = True) -> int:
        """In-memory list push"""
        list_key = f"_list:{key}"
        with self._lock:
            if list_key not in self._memory_fallback:
                self._memory_fallback[list_key] = []
            if left:
                self._memory_fallback[list_key].insert(0, value)
            else:
                self._memory_fallback[list_key].append(value)
            return len(self._memory_fallback[list_key])
    
    def _memory_fallback_list_pop(self, key: str) -> Optional[str]:
        """In-memory list pop (FIFO)"""
        list_key = f"_list:{key}"
        with self._lock:
            if list_key in self._memory_fallback and self._memory_fallback[list_key]:
                return self._memory_fallback[list_key].pop()
            return None
    
    # ============================================================
    # Health Check
    # ============================================================
    
    def health_check(self) -> dict:
        """For your /api/v1/health/deep endpoint"""
        if not self.config.enabled:
            return {'redis': 'disabled'}
        
        try:
            start = time.time()
            self.ping()
            latency_ms = (time.time() - start) * 1000
            
            # Get memory info
            info = self._request("GET", "/info/memory")
            
            return {
                'redis': 'healthy',
                'latency_ms': round(latency_ms, 2),
                'used_memory_mb': round(info.get('result', {}).get('used_memory', 0) / 1024 / 1024, 2),
                'fallback_active': not self._available,
            }
        except Exception as e:
            return {
                'redis': 'degraded',
                'error': str(e),
                'fallback_active': True,
            }


class RedisConnectionError(Exception):
    """Redis is unreachable — system will use fallback"""
    pass
