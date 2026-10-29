"""
Rate Limiter v1.0 — Database-backed rate limiting
- Calls Supabase check_rate_limit() function
- Auto-blocks abusers at 3x limit
- Graceful when Supabase is unavailable
"""

import logging
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("kanyoza.utils.rate_limiter")


class RateLimiter:
    """Database-backed rate limiter."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._fallback_counts = {}  # In-memory fallback if Supabase down
        self._fallback_lock = threading.Lock()  # Protect _fallback_counts

    def is_allowed(self, identifier: str, request_type: str) -> bool:
        """
        Check if a request is allowed.
        Returns True if allowed, False if rate limited.
        """
        if self.supabase:
            try:
                result = self.supabase.rpc('check_rate_limit', {
                    'p_identifier': identifier,
                    'p_request_type': request_type,
                }).execute()
                if result.data is not None:
                    return result.data
            except Exception as e:
                logger.warning(f"Rate limit check failed, using fallback: {e}")

        # Fallback: simple in-memory rate limiting (thread-safe)
        key = f"{identifier}:{request_type}"
        now = datetime.now().timestamp()

        with self._fallback_lock:
            if key not in self._fallback_counts:
                self._fallback_counts[key] = []

            # Clean old entries
            self._fallback_counts[key] = [
                t for t in self._fallback_counts[key]
                if now - t < 60
            ]

            if len(self._fallback_counts[key]) >= 10:
                return False

            self._fallback_counts[key].append(now)
            return True

    def unblock(self, identifier: str) -> bool:
        """Manually unblock a user."""
        if self.supabase:
            try:
                result = self.supabase.rpc('unblock_user', {
                    'p_identifier': identifier,
                }).execute()
                return result.data if result.data is not None else False
            except Exception as e:
                logger.error(f"Failed to unblock: {e}")
        return False

    def get_config(self) -> list:
        """Get current rate limit configuration."""
        if self.supabase:
            try:
                result = self.supabase.table('rate_limit_config').select('*').execute()
                return result.data if result.data else []
            except Exception:
                pass
        return []


rate_limiter = RateLimiter()
