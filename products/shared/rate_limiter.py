# products/shared/rate_limiter.py
"""
Per-API-key rate limiter.
In-memory sliding window (upgrade to Redis for production).
"""

import time
import threading
from collections import defaultdict

_lock = threading.Lock()
_windows: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(
    api_key: str,
    endpoint: str,
    max_requests: int = 100,
    window_seconds: int = 3600,
) -> bool:
    """
    Check if an API key is within its rate limit.
    
    Returns True if allowed, False if rate limited.
    """
    key = f"{api_key}:{endpoint}"
    now = time.time()

    with _lock:
        # Remove timestamps outside the window
        _windows[key] = [t for t in _windows[key] if now - t < window_seconds]

        if len(_windows[key]) >= max_requests:
            return False

        _windows[key].append(now)
        return True


def get_usage(api_key: str, endpoint: str, window_seconds: int = 3600) -> int:
    """Get current usage count for an API key."""
    key = f"{api_key}:{endpoint}"
    now = time.time()

    with _lock:
        _windows[key] = [t for t in _windows[key] if now - t < window_seconds]
        return len(_windows[key])
