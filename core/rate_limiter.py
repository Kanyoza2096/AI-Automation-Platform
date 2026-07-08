# core/rate_limiter.py — NEW
from core.redis_client import UpstashRedisClient

class RedisRateLimiter:
    """Workspace-aware rate limiter using Redis"""
    
    def __init__(self, redis: UpstashRedisClient):
        self.redis = redis
        self._local_counters = {}
    
    def is_allowed(self, workspace_id: str, action: str, max_per_minute: int) -> bool:
        """Check if action is within rate limit for this workspace"""
        key = f"ratelimit:{workspace_id}:{action}"
        
        try:
            current = self.redis.incr(key)
            if current == 1:
                # First request — set expiry
                self.redis.expire(key, 60)
            return current <= max_per_minute
        except Exception:
            # Fallback: simple counter with time window
            return self._local_check(key, max_per_minute)
    
    def _local_check(self, key: str, max_per_minute: int) -> bool:
        now = time.time()
        if key not in self._local_counters:
            self._local_counters[key] = []
        # Clean old entries
        self._local_counters[key] = [
            t for t in self._local_counters[key] 
            if now - t < 60
        ]
        if len(self._local_counters[key]) < max_per_minute:
            self._local_counters[key].append(now)
            return True
        return False
