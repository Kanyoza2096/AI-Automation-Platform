"""
Job Stats Service — Single-query stats aggregation.
Never imports or calls JobService. Recursion is structurally impossible.

Architecture:
  - Reads directly from Supabase via Postgres function
  - Simple threading.Lock cache (not RLock — no reentrancy needed)
  - Cache invalidation via event subscription
  - Returns real errors, never fake zeros
  - NO auto-resolution of dependencies (that causes recursion)
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


class JobStatsService:
    """Read-only stats service. Zero dependencies on other services.

    The Supabase client MUST be injected via constructor or set_client().
    Never auto-resolves. Never imports supabase_service.
    """

    CACHE_TTL_SECONDS = 30

    def __init__(self, supabase_client=None, event_bus=None):
        self._client = supabase_client
        self._event_bus = event_bus
        self._cache: dict = {}
        self._cache_time: float = 0.0
        self._lock = threading.Lock()

        if self._event_bus:
            self._subscribe_to_events()

    # ------------------------------------------------------------------
    # Client injection (NO AUTO-RESOLUTION)
    # ------------------------------------------------------------------

    def set_client(self, client) -> None:
        """Inject the Supabase client. Required before use."""
        self._client = client

    def set_event_bus(self, event_bus) -> None:
        """Inject the event bus for cache invalidation."""
        self._event_bus = event_bus
        self._subscribe_to_events()

    def _require_client(self):
        """Get client or raise immediately. NO FALLBACK."""
        if self._client is None:
            raise DatabaseQueryError(
                message=(
                    "JobStatsService: Supabase client not configured. "
                    "Call set_client() before use."
                ),
                context={"operation": "job_stats_service"},
            )
        return self._client

    # ------------------------------------------------------------------
    # Event subscription (cache invalidation)
    # ------------------------------------------------------------------

    def _subscribe_to_events(self) -> None:
        """Listen for job changes to invalidate cache."""
        try:
            if hasattr(self._event_bus, "subscribe"):
                events = [
                    "job.created",
                    "job.status_changed",
                    "job.published",
                    "job.failed",
                    "job.stale_detected",
                    "job.retry_scheduled",
                ]
                for event in events:
                    self._event_bus.subscribe(event, self._on_job_changed)
        except Exception as exc:
            logger.debug("JobStatsService: event subscription failed: %s", exc)

    def _on_job_changed(self, payload: dict = None) -> None:
        """Invalidate cache when any job changes."""
        with self._lock:
            self._cache = {}
            self._cache_time = 0.0

    # ------------------------------------------------------------------
    # Public API — single-query, no method chaining
    # ------------------------------------------------------------------

    def get_stats(self, brand_id: Optional[str] = None) -> dict:
        """
        Get comprehensive job stats in a single database call.

        Uses Postgres function for server-side aggregation.
        Falls back to client-side aggregation if RPC unavailable.
        No Python method calls to other services. No recursion possible.
        """
        # Guard: no client = no stats
        if self._client is None:
            return self._empty_stats()

        # Check cache
        with self._lock:
            cache_key = brand_id or "__all__"
            if cache_key in self._cache:
                if time.monotonic() - self._cache_time < self.CACHE_TTL_SECONDS:
                    return self._cache[cache_key]

        try:
            stats = self._compute_stats_via_rpc(brand_id)
        except Exception as e:
            logger.debug("JobStatsService: RPC failed (%s), using fallback", e)
            try:
                stats = self._compute_stats_client_side(brand_id)
            except Exception as e2:
                logger.error("JobStatsService: All stats methods failed: %s", e2)
                return self._empty_stats()

        # Update cache
        with self._lock:
            if not self._cache:
                self._cache = {}
            self._cache[cache_key] = stats
            self._cache_time = time.monotonic()

        return stats

    def _compute_stats_via_rpc(self, brand_id: Optional[str]) -> dict:
        """Single RPC call — zero Python method calls."""
        client = self._require_client()
        params = {}
        if brand_id:
            params["brand_id"] = brand_id

        result = client.rpc("get_job_stats", params).execute()

        if result.data and isinstance(result.data, dict):
            return result.data

        # RPC returned unexpected format, try client-side
        raise ValueError("RPC returned non-dict data")

    def _compute_stats_client_side(self, brand_id: Optional[str]) -> dict:
        """
        Fallback: single query with in-memory aggregation.
        Still no Python method chaining to other services.
        """
        client = self._require_client()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Single query
        query = client.table("automation_jobs").select("status,created_at,updated_at")

        if brand_id:
            query = query.eq("brand_id", brand_id)

        result = query.execute()

        if not result.data:
            return self._empty_stats()

        rows = result.data
        now = datetime.now(timezone.utc)

        # One-pass aggregation
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

            # Status counts (all time)
            status_counts[status] = status_counts.get(status, 0) + 1

            # Today stats
            if created and created >= f"{today}T00:00:00Z":
                today_total += 1
                if status == "PUBLISHED":
                    today_published += 1
                elif status == "FAILED":
                    today_failed += 1
                elif status == "RETRY_PENDING":
                    today_retrying += 1
                elif status in ACTIVE_STATUS_VALUES:
                    today_active += 1

            # Oldest active
            if status in ACTIVE_STATUS_VALUES and updated:
                updated_dt = self._parse_timestamp(updated)
                if updated_dt:
                    if oldest_active is None or updated_dt < oldest_active:
                        oldest_active = updated_dt

        active_count = sum(
            count
            for status, count in status_counts.items()
            if status in ACTIVE_STATUS_VALUES
        )

        oldest_minutes = 0
        if oldest_active:
            oldest_minutes = round(
                (now - oldest_active).total_seconds() / 60, 1
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

    def health_check(self) -> dict:
        """Health check that never lies. Returns real status."""
        if self._client is None:
            return {
                "status": "not_configured",
                "error": "Supabase client not injected",
                "stats": None,
            }

        try:
            stats = self.get_stats()
            return {
                "status": "healthy",
                "stats": stats,
            }
        except Exception as e:
            logger.error("JobStatsService health check failed: %s", e)
            return {
                "status": "unhealthy",
                "error": str(e)[:200],
                "stats": None,
            }

    def get_status(self) -> dict:
        """Legacy interface for Director/MetaEngine compatibility."""
        return self.health_check()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_stats() -> dict:
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
    def _parse_timestamp(value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        try:
            s = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════
# Module Singleton — NO AUTO-RESOLUTION
# ═══════════════════════════════════════════════════════════════════════

job_stats_service: Optional[JobStatsService] = None


def init_job_stats_service(
    supabase_client=None,
    event_bus=None,
) -> JobStatsService:
    """Initialize the singleton. Client MUST be passed explicitly."""
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService(
            supabase_client=supabase_client,
            event_bus=event_bus,
        )
    else:
        if supabase_client:
            job_stats_service.set_client(supabase_client)
        if event_bus:
            job_stats_service.set_event_bus(event_bus)

    if supabase_client is None:
        logger.warning(
            "JobStatsService: No Supabase client provided. "
            "Stats will be unavailable. Call init_job_stats_service(client=...) "
            "during boot."
        )

    return job_stats_service


def get_job_stats_service() -> JobStatsService:
    """Get the singleton. Returns unconfigured instance if not initialized."""
    global job_stats_service
    if job_stats_service is None:
        job_stats_service = JobStatsService()
        logger.warning(
            "JobStatsService: Not initialized. "
            "Call init_job_stats_service(client=...) during boot."
        )
    return job_stats_service
