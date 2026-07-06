# products/shared/billing.py
"""
Usage tracking for billing.
Tracks API calls per key for later invoicing.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger("kanyoza.products.shared.billing")

# In-memory tracking (upgrade to Supabase for production)
_usage: dict[str, list[dict]] = {}


def track_usage(api_key: str, endpoint: str):
    """Record an API call for billing."""
    record = {
        "api_key": api_key[:20] + "...",  # Partial for privacy
        "endpoint": endpoint,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if api_key not in _usage:
        _usage[api_key] = []

    _usage[api_key].append(record)

    logger.debug(f"Usage tracked: {endpoint}")


def get_usage_count(api_key: str, since_days: int = 30) -> int:
    """Get total usage count for an API key."""
    if api_key not in _usage:
        return 0

    cutoff = datetime.now(timezone.utc).timestamp() - (since_days * 86400)

    count = 0
    for record in _usage[api_key]:
        record_time = datetime.fromisoformat(record["timestamp"]).timestamp()
        if record_time > cutoff:
            count += 1

    return count
