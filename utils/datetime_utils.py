"""
Datetime utilities — UTC-first, timezone-aware helpers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def utcnow() -> datetime:
    """Return the current UTC-aware datetime."""
    return datetime.now(timezone.utc)


def format_utc(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """
    Format *dt* as a UTC string.
    Returns "—" when *dt* is None.
    """
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(fmt)


def parse_utc(value: str) -> Optional[datetime]:
    """
    Parse an ISO-8601 datetime string into a UTC-aware datetime.
    Returns None on parse failure instead of raising.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def humanize_delta(dt: datetime) -> str:
    """
    Return a human-readable relative time string.
    e.g. "just now", "5 minutes ago", "2 hours ago", "3 days ago"
    """
    now = utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta: timedelta = now - dt
    seconds = int(delta.total_seconds())

    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


def next_occurrence_utc(hour: int, minute: int = 0) -> datetime:
    """
    Return the next UTC datetime that falls on *hour*:*minute*.
    If that time has already passed today, returns tomorrow's occurrence.
    """
    now = utcnow()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
