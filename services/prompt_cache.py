# services/prompt_cache.py — MODIFY your existing prompt cache
from typing import Optional

from core.redis_client import UpstashRedisClient

class RedisPromptCache:
    """Redis-backed prompt cache with fallback"""
    
    def __init__(self, redis: UpstashRedisClient):
        self.redis = redis
        self._local_cache = {}  # In-memory fallback
    
    def get(self, profile_id: str) -> Optional[str]:
        cache_key = f"prompt:{profile_id}"
        value = self.redis.get(cache_key)
        if value:
            return value
        return self._local_cache.get(cache_key)
    
    def set(self, profile_id: str, prompt: str, ttl_seconds: int = 1800):
        cache_key = f"prompt:{profile_id}"
        self.redis.set(cache_key, prompt, ex=ttl_seconds)
        self._local_cache[cache_key] = prompt  # Backup
    
    def invalidate(self, profile_id: str):
        cache_key = f"prompt:{profile_id}"
        self.redis.delete(cache_key)
        self._local_cache.pop(cache_key, None)
