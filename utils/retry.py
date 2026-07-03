"""
Smart retry decorator with exponential backoff and jitter.
"""

import random
import time
import logging
from functools import wraps
from typing import Any, Tuple, Type

logger = logging.getLogger("kanyoza.utils.retry")


def smart_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    backoff_factor: float = 2.0,
    fallback_value: Any = None,
    use_fallback: bool = False,
    no_retry_exceptions: Tuple[Type[Exception], ...] = (),
):
    """
    Retry a function on failure with exponential backoff + jitter.

    Args:
        max_retries: Total attempts (first call + retries).
        base_delay: Seconds before first retry.
        exceptions: Exception types that trigger a retry.
        backoff_factor: Delay multiplier per retry.
        fallback_value: Return value when all retries fail (requires use_fallback=True).
        use_fallback: Return fallback_value instead of re-raising on total failure.
        no_retry_exceptions: Exception types that are re-raised immediately without
            retrying (e.g. GeminiRateLimited — retrying during a quota window wastes
            time and quota).
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except no_retry_exceptions:
                    # Do not retry quota / rate-limit errors — propagate immediately
                    # so the caller can decide whether to skip or wait.
                    raise
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_retries - 1:
                        delay = base_delay * (backoff_factor ** attempt)
                        delay += random.uniform(0, delay * 0.3)
                        logger.warning(
                            "Retry %d/%d for %s (%.1fs) — %s",
                            attempt + 1, max_retries, func.__name__, delay, exc,
                        )
                        time.sleep(delay)
                    else:
                        logger.error("All %d retries failed for %s: %s", max_retries, func.__name__, exc)

            if use_fallback:
                return fallback_value
            raise last_exc

        return wrapper
    return decorator
