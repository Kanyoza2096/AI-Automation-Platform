"""
Job Stats Service — Single-query stats aggregation.
Never imports or calls JobService. Recursion is structurally impossible.

Architecture:
  - Reads directly from Supabase via Postgres function
  - Simple threading.Lock cache (not RLock — no reentrancy needed)
  - Cache invalidation via event subscription
  - Returns real errors, never fake zeros
  - NO auto-resolution of dependencies (that causes recursion)
  - Supabase client MUST be injected via constructor or set_client()
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from core.errors import DatabaseQueryError

logger = logging.getLogger("kanyoza.services.job_stats_service")

# ═══════════════════════════════════════════════════════════════════════
# Single source of truth — hardcoded to avoid circular imports
# Must match models.automation_job.ACTIVE_STATUS_VALUES exactly
# ═══════════════════════════════════════════════════════════════════════

ACTIVE_STATUS_VALUES = (
    "CREATED",
    "QUEUED",
    "RUNNING",
    "GENERATING",
    "VALIDATING",
    "RENDERING",
    "READY",
    "PUBLISHING",
    "RETRY_PENDING",
    "STALE",
)

TERMINAL_STATUS_VALUES = (
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
)

# ═══════════════════════════════════════════════════════════════════════
# JobStatsService
# ═══════════════════════════════════════════════════════════════════════


class JobStatsService:
    """Read-only stats service. Zero dependencies on other services.

    The Supabase client MUST be injected via constructor or set_client().
    Never auto-resolves. Never imports supabase_service, JobService, or
    any other Kanyoza service module.

    Usage:
        # At boot time (lifecycle.py):
        stats = JobStatsService(supabase_client=client, event_bus=bus)

        # At runtime:
        stats = get_job_stats_service()
        data = stats.get_stats(brand_id="brand_123")
        health = stats.health_check()
    """

    CACHE_TTL_SECONDS = 30

    def __init__(
        self,
        supabase_client=None,
        event_bus=None,
    ) -> None:
        """Initialize with injected dependencies.

        Args:
            supabase_client: Supabase client instance (required for operation).
            event_bus: Optional EventBus for cache invalidation on job changes.
        """
        self._client = supabase_client
        self._event_bus = event_bus
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._lock = threading.Lock()

        if self._event_bus is not None:
            self._subscribe_to_events()

        if supabase_client is None:
            logger.warning(
                "JobStatsService: No Supabase client provided. "
                "Stats will return empty data until wired. "
                "Call set_client() or pass client to constructor."
            )
        else:
            logger.info("JobStatsService: initialized with Supabase client")

    # ------------------------------------------------------------------
    # Dependency Injection (NO AUTO-RESOLUTION)
    # ------------------------------------------------------------------

    def set_client(self, client) -> None:
        """Inject the Supabase client. Required before use if not
        passed to constructor."""
        self._client = client
        logger.info("JobStatsService: Supabase client injected")

    def set_event_bus(self, event_bus) -> None:
        """Inject the event bus for cache invalidation on job changes."""
        self._event_bus = event_bus
        self._subscribe_to_events()

    def _require_client(self):
        """Get client or raise immediately. NO FALLBACK RESOLUTION.

        This is the critical design decision that prevents recursion.
        We never call supabase_service or any other module to resolve
        the client. It must be handed to us.
        """
        if self._client is None:
            raise DatabaseQueryError(
                message=(
                    "JobStatsService: Supabase client not configured. "
                    "Call set_client() or pass client to constructor "
                    "before calling get_stats()."
                ),
                context={"operation": "job_stats_service"},
            )
        return self._client

    # ------------------------------------------------------------------
    # Event Subscription (Cache Invalidation)
    # ------------------------------------------------------------------

    def _subscribe_to_events(self) -> None:
        """Listen for job changes to invalidate the stats cache.

        When any job is created, transitions, publishes, fails, or
        recovers, we drop the cache so the next get_stats() call
        fetches fresh data.
        """
        if self._event_bus is None:
            return

        events = [
            "job.created",
            "job.status_changed",
            "job.published",
            "job.failed",
            "job.stale_detected",
            "job.recovered_and_retried",
            "job.retry_scheduled",
        ]

        try:
            if hasattr(self._event_bus, "subscribe"):
                for event in events:
                    self._event_bus.subscribe(event, self._on_job_changed)
                logger.debug(
                    "JobStatsService: subscribed to %d job events", len(events)
                )
            else:
                logger.debug("JobStatsService: event_bus has no subscribe method")
        except Exception as exc:
            logger.debug("JobStatsService: event subscription failed: %s", exc)

    def _on_job_changed(self, payload: dict = None) -> None:
        """Invalidate cache when any job changes.

        Thread-safe: uses the same lock as get_stats().
        """
        with self._lock:
            self._cache.clear()
            self._cache_time = 0.0

    def invalidate_cache(self) -> None:
        """Public method to force cache invalidation."""
        self._on_job_changed()

    # ------------------------------------------------------------------
    # Public API — Single Query, No Method Chaining
    # ------------------------------------------------------------------

    def get_stats(self, brand_id: Optional[str] = None) -> dict:
        """
        Get comprehensive job statistics in a single database call.

        Uses the get_job_stats Postgres function for server-side
        aggregation. Falls back to client-side aggregation if the RPC
        is unavailable.

        No Python method calls to other Kanyoza services.
        No recursion is possible because this class has zero
        dependencies on any other service module.

        Args:
            brand_id: Optional brand filter. If None, returns all jobs.

        Returns:
            dict with keys: today, status_counts, active_jobs,
            oldest_active_minutes

        Raises:
            DatabaseQueryError: If Supabase client is not configured.
        """
        # Short-circuit: no client = no stats
        if self._client is None:
            logger.warning("JobStatsService: get_stats() called without client")
            return self._empty_stats()

        # Check cache
        cache_key = brand_id or "__all__"
        with self._lock:
            if cache_key in self._cache:
                if time.monotonic() - self._cache_time < self.CACHE_TTL_SECONDS:
                    return self._cache[cache_key]

        # Compute fresh stats
        try:
            stats = self._compute_stats_via_rpc(brand_id)
        except Exception as e:
            logger.debug(
                "JobStatsService: RPC failed (%s), trying client-side fallback", e
            )
            try:
                stats = self._compute_stats_client_side(brand_id)
            except Exception as e2:
                logger.error("JobStatsService: all stats methods failed: %s", e2)
                return self._empty_stats()

        # Update cache
        with self._lock:
            self._cache[cache_key] = stats
            self._cache_time = time.monotonic()

        return stats

    def get_today_stats(self, brand_id: Optional[str] = None) -> dict:
        """Get today's stats only. Convenience wrapper."""
        full_stats = self.get_stats(brand_id=brand_id)
        return full_stats.get("today", self._empty_stats()["today"])

    def get_status_counts(self, brand_id: Optional[str] = None) -> dict:
        """Get status counts only. Convenience wrapper."""
        full_stats = self.get_stats(brand_id=brand_id)
        return full_stats.get("status_counts", {})

    def health_check(self) -> dict:
        """
        Health check that returns REAL status. Never lies.

        Returns:
            dict with status and optional stats/error.
            status values: "healthy", "degraded", "unhealthy",
            "not_configured"
        """
        if self._client is None:
            return {
                "status": "not_configured",
                "error": "Supabase client not injected. Call set_client() during boot.",
                "stats": None,
            }

        try:
            stats = self.get_stats()
            return {
                "status": "healthy",
                "stats": stats,
            }
        except DatabaseQueryError as e:
            logger.error("JobStatsService health check failed: %s", e)
            return {
                "status": "unhealthy",
                "error": str(e)[:200],
                "stats": None,
            }
        except Exception as e:
            logger.error("JobStatsService health check failed: %s", e)
            return {
                "status": "degraded",
                "error": str(e)[:200],
                "stats": None,
            }

    def get_status(self) -> dict:
        """Legacy interface for Director/MetaEngine compatibility.

        Returns the same dict as health_check().
        """
        return self.health_check()

    # ------------------------------------------------------------------
    # Private — Stats Computation
    # ------------------------------------------------------------------

    def _compute_stats_via_rpc(self, brand_id: Optional[str]) -> dict:
        """
        Single RPC call to get_job_stats Postgres function.

        This is the preferred path. One database round-trip.
        Zero Python method calls to other services.
        """
        client = self._require_client()
        params: dict = {}
        if brand_id:
            params["brand_id"] = brand_id

        result = client.rpc("get_job_stats", params).execute()

        if result.data and isinstance(result.data, dict):
            return self._normalize_stats(result.data)

        # RPC returned unexpected format
        raise ValueError(
            f"RPC returned {type(result.data).__name__}, expected dict"
        )

    def _compute_stats_client_side(self, brand_id: Optional[str]) -> dict:
        """
        Fallback: single query with in-memory aggregation.

        Used when the get_job_stats Postgres function is unavailable.
        Still makes only ONE database query. Still no Python method
        chaining to other Kanyoza services.
        """
        client = self._require_client()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Single query — select only the columns we need
        query = client.table("automation_jobs").select("status,created_at,updated_at")

        if brand_id:
            query = query.eq("brand_id", brand_id)

        result = query.execute()

        if not result.data:
            return self._empty_stats()

        rows = result.data
        now_utc = datetime.now(timezone.utc)

        # One-pass aggregation over all rows
        today_total = 0
        today_published = 0
        today_failed = 0
        today_retrying = 0
        today_active = 0
        status_counts: dict[str, int] = {}
        oldest_active: Optional[datetime] = None

        for row in rows:
            status = row.get("status", "unknown")
            created = row.get("created_at")
            updated = row.get("updated_at")

            # All-time status counts
            status_counts[status] = status_counts.get(status, 0) + 1

            # Today-specific stats
            if created and self._is_today(created, today):
                today_total += 1
                if status == "PUBLISHED":
                    today_published += 1
                elif status == "FAILED":
                    today_failed += 1
                elif status == "RETRY_PENDING":
                    today_retrying += 1
                elif status in ACTIVE_STATUS_VALUES:
                    today_active += 1

            # Track oldest active job
            if status in ACTIVE_STATUS_VALUES and updated:
                updated_dt = self._parse_timestamp(updated)
                if updated_dt is not None:
                    if oldest_active is None or updated_dt < oldest_active:
                        oldest_active = updated_dt

        # Count active jobs from status_counts
        active_count = sum(
            count
            for status, count in status_counts.items()
            if status in ACTIVE_STATUS_VALUES
        )

        # Calculate oldest active in minutes
        oldest_minutes = 0.0
        if oldest_active is not None:
            oldest_minutes = round(
                (now_utc - oldest_active).total_seconds() / 60.0, 1
            )

        return {
            "today": {
                "total": today_total,
                "published": today_published,
                "failed": today_failed,
                "retrying": today_retrying,
                "active": today_active,
            },
            "status_counts": status_counts,
            "active_jobs": active_count,
            "oldest_active_minutes": oldest_minutes,
        }

    # ------------------------------------------------------------------
    # Private — Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_stats() -> dict:
        """Return empty stats structure. Used when no data is available."""
        return {
            "today": {
                "total": 0,
                "published": 0,
                "failed": 0,
                "retrying": 0,
                "active": 0,
            },
            "status_counts": {},
            "active_jobs": 0,
            "oldest_active_minutes": 0,
        }

    @staticmethod
    def _normalize_stats(data: dict) -> dict:
        """Ensure the stats dict has all expected keys with defaults."""
        defaults = JobStatsService._empty_stats()
        if not isinstance(data, dict):
            return defaults

        # Ensure 'today' sub-dict
        today = data.get("today", {})
        if not isinstance(today, dict):
            today = {}
        data["today"] = {
            "total": today.get("total", 0),
            "published": today.get("published", 0),
            "failed": today.get("failed", 0),
            "retrying": today.get("retrying", 0),
            "active": today.get("active", 0),
        }

        # Ensure other keys
        data.setdefault("status_counts", {})
        data.setdefault("active_jobs", 0)
        data.setdefault("oldest_active_minutes", 0)

        return data

    @staticmethod
    def _is_today(timestamp_str: str, today_str: str) -> bool:
        """Check if a timestamp string falls on today's date."""
        if not timestamp_str:
            return False
        # Timestamps from Supabase look like "2026-08-11T19:25:40+00:00"
        # Today string looks like "2026-08-11"
        return timestamp_str >= f"{today_str}T00:00:00"

    @staticmethod
    def _parse_timestamp(value) -> Optional[datetime]:
        """Safely parse a timestamp from various formats.

        Returns timezone-aware UTC datetime or None.
        """
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc)
            except (ValueError, OSError):
                return None

        if isinstance(value, str):
            try:
                # Handle ISO format with or without timezone
                s = value.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, TypeError):
                pass

        return None


# ═══════════════════════════════════════════════════════════════════════
# Module Singleton
# ═══════════════════════════════════════════════════════════════════════

job_stats_service: Optional[JobStatsService] = None


def init_job_stats_service(
    supabase_client=None,
    event_bus=None,
) -> JobStatsService:
    """Initialize the JobStatsService singleton.

    MUST be called during application boot (lifecycle.py).
    The Supabase client is REQUIRED and must be passed explicitly.

    Args:
        supabase_client: Supabase client instance.
        event_bus: Optional EventBus for cache invalidation.

    Returns:
        The configured JobStatsService singleton.

    Example:
        # In lifecycle.py _boot_job_service():
        from services.job_stats_service import init_job_stats_service
        init_job_stats_service(supabase_client=client, event_bus=bus)
    """
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService(
            supabase_client=supabase_client,
            event_bus=event_bus,
        )
        logger.info("JobStatsService: singleton initialized")
    else:
        # Update existing instance with new clients
        if supabase_client is not None:
            job_stats_service.set_client(supabase_client)
        if event_bus is not None:
            job_stats_service.set_event_bus(event_bus)
        logger.info("JobStatsService: singleton updated with new clients")

    if supabase_client is None:
        logger.warning(
            "JobStatsService: No Supabase client provided. "
            "Stats will return empty data until wired. "
            "Call init_job_stats_service(client=...) during boot."
        )

    return job_stats_service


def get_job_stats_service() -> JobStatsService:
    """Get the JobStatsService singleton.

    If not yet initialized, returns an unconfigured instance
    that will return empty stats until wired.

    Returns:
        JobStatsService instance (may be unconfigured).
    """
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService()
        logger.warning(
            "JobStatsService: get_job_stats_service() called before "
            "init_job_stats_service(). Stats will return empty data. "
            "Call init_job_stats_service(client=...) during boot."
        )

    return job_stats_service


# ═══════════════════════════════════════════════════════════════════════
# Exports
# ═══════════════════════════════════════════════════════════════════════

__all__ = [
    "JobStatsService",
    "job_stats_service",
    "init_job_stats_service",
    "get_job_stats_service",
    "ACTIVE_STATUS_VALUES",
    "TERMINAL_STATUS_VALUES",
]
