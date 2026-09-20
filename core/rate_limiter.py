# core/rate_limiter.py
import time
import logging
import threading
from typing import Optional
from core.redis_client import UpstashRedisClient

logger = logging.getLogger("kanyoza.core.rate_limiter")


class RateLimiter:
    """Workspace-aware and general rate limiter using Redis with thread-safe in-memory fallback.
    
    Config is persisted to Supabase rate_limits table and cached in-memory.
    Falls back to defaults when database is unavailable.
    """

    def __init__(self, redis: Optional[UpstashRedisClient] = None):
        self.redis = redis
        self._local_counters = {}
        self._lock = threading.Lock()
        
        # Config cache — loaded from DB on first use
        self._config_cache: dict = {}
        self._config_loaded = False
        self._config_lock = threading.Lock()

    # ── Default configs ────────────────────────────────────────────────
    
    DEFAULT_LIMITS = {
        "api_per_user": 30,        # General API calls per user per minute
        "ai_generate": 10,         # AI generation requests
        "publish_post": 5,         # Manual publish requests
        "auth_attempts": 10,       # Login/auth attempts
        "webhook_calls": 60,       # Webhook invocations
        "file_upload": 10,         # File uploads
    }

    # ── Rate limiting ──────────────────────────────────────────────────

    def is_allowed(self, key: str, max_per_minute: int) -> bool:
        """Check if action is allowed for the key."""
        if self.redis and self.redis.is_available:
            try:
                redis_key = f"ratelimit:{key}"
                current = self.redis.incr(redis_key)
                if current == 1:
                    self.redis.expire(redis_key, 60)
                return current <= max_per_minute
            except Exception as e:
                logger.warning("Redis rate limit check failed, using in-memory fallback: %s", e)

        return self._local_check(key, max_per_minute)

    def _local_check(self, key: str, max_per_minute: int) -> bool:
        now = time.time()
        with self._lock:
            if key not in self._local_counters:
                self._local_counters[key] = []
            self._local_counters[key] = [
                t for t in self._local_counters[key]
                if now - t < 60
            ]
            if len(self._local_counters[key]) < max_per_minute:
                self._local_counters[key].append(now)
                return True
            return False

    # ── Config management ──────────────────────────────────────────────

    def get_config(self, workspace_id: str = "global") -> dict:
        """Get rate limit configuration for a workspace."""
        with self._config_lock:
            if workspace_id in self._config_cache:
                return self._config_cache[workspace_id]
        
        # Try to load from database
        config = self._load_config_from_db(workspace_id)
        
        with self._config_lock:
            self._config_cache[workspace_id] = config
        
        return config

    def update_config(self, workspace_id: str, updates: dict) -> dict:
        """Update rate limit configuration for a workspace."""
        current = self.get_config(workspace_id)
        
        # Merge updates — only allow valid keys
        for key, value in updates.items():
            if key in self.DEFAULT_LIMITS and isinstance(value, int) and value >= 1:
                current[key] = value
        
        # Persist to database
        self._save_config_to_db(workspace_id, current)
        
        # Update cache
        with self._config_lock:
            self._config_cache[workspace_id] = current
        
        return current

    def get_limit(self, action: str, workspace_id: str = "global") -> int:
        """Get the limit for a specific action in a workspace."""
        config = self.get_config(workspace_id)
        return config.get(action, self.DEFAULT_LIMITS.get(action, 30))

    def _load_config_from_db(self, workspace_id: str) -> dict:
        """Load rate limit config from Supabase."""
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = supabase_service.client.table("rate_limits") \
                    .select("*") \
                    .eq("workspace_id", workspace_id) \
                    .limit(1) \
                    .execute()
                if result.data and len(result.data) > 0:
                    row = result.data[0]
                    loaded = {}
                    for key in self.DEFAULT_LIMITS:
                        if key in row and row[key] is not None:
                            loaded[key] = int(row[key])
                    # Merge with defaults for any missing keys
                    return {**self.DEFAULT_LIMITS, **loaded}
        except Exception as e:
            logger.debug("Failed to load rate limits from DB: %s", e)
        
        return dict(self.DEFAULT_LIMITS)

    def _save_config_to_db(self, workspace_id: str, config: dict) -> None:
        """Save rate limit config to Supabase."""
        try:
            from services.supabase import supabase_service
            from datetime import datetime, timezone
            if supabase_service and supabase_service.client:
                supabase_service.client.table("rate_limits") \
                    .upsert({
                        "workspace_id": workspace_id,
                        **config,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }) \
                    .execute()
        except Exception as e:
            logger.warning("Failed to save rate limits to DB: %s", e)

    def unblock(self, identifier: str) -> bool:
        """Unblock a rate-limited key."""
        try:
            if self.redis and self.redis.is_available:
                redis_key = f"ratelimit:{identifier}"
                self.redis.delete(redis_key)
        except Exception:
            pass
        
        with self._lock:
            if identifier in self._local_counters:
                del self._local_counters[identifier]
                return True
        return False
