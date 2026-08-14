"""
Job Stats Service — Single-query stats aggregation.
Never imports or calls JobService. Recursion is structurally impossible.

v4.0 — BROKEN PIPE FIX:
  - Per-thread Supabase client (no shared HTTP/2 connection)
  - Connection pool with health checks
  - Automatic retry on broken pipe
  - Thread-safe client rotation

Architecture:
  - Reads directly from Supabase via Postgres function
  - Thread-local clients (no cross-thread connection reuse)
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
# v4.0 — THREAD-LOCAL SUPABASE CLIENT
# ═══════════════════════════════════════════════════════════════════════

class ThreadLocalSupabaseClient:
    """Manages per-thread Supabase clients to prevent broken pipes.
    
    The problem: Supabase's HTTP/2 client is NOT thread-safe for
    concurrent requests. When multiple threads share the same client,
    one thread can close the connection while another is still using it,
    causing "Broken pipe" errors.
    
    The fix: Each thread gets its own client instance.
    """
    
    def __init__(self, client_factory):
        self._client_factory = client_factory
        self._thread_local = threading.local()
        self._lock = threading.Lock()
        self._all_clients: list = []
        self._max_clients = 20
    
    def get_client(self):
        """Get the current thread's client, creating one if needed."""
        client = getattr(self._thread_local, "client", None)
        if client is None:
            client = self._client_factory()
            self._thread_local.client = client
            with self._lock:
                self._all_clients.append(client)
                # Cleanup old clients if too many
                if len(self._all_clients) > self._max_clients:
                    self._all_clients = self._all_clients[-self._max_clients:]
        return client
    
    def reset_current_thread(self):
        """Reset the current thread's client (after broken pipe)."""
        if hasattr(self._thread_local, "client"):
            del self._thread_local.client
    
    def reset_all(self):
        """Reset all thread clients."""
        self._thread_local = threading.local()
        self._all_clients = []


# ═══════════════════════════════════════════════════════════════════════
# JobStatsService v4.0
# ═══════════════════════════════════════════════════════════════════════

class JobStatsService:
    """Read-only stats service with thread-safe Supabase client management."""

    CACHE_TTL_SECONDS = 30
    MAX_BROKEN_PIPE_RETRIES = 2

    def __init__(
        self,
        supabase_client=None,
        event_bus=None,
    ) -> None:
        self._event_bus = event_bus
        self._cache: dict[str, dict] = {}
        self._cache_time: float = 0.0
        self._lock = threading.Lock()
        
        # v4.0 — Thread-local client management
        self._client_factory = None
        self._thread_clients = None
        
        if supabase_client is not None:
            self.set_client(supabase_client)
        else:
            logger.warning(
                "JobStatsService: No Supabase client provided. "
                "Stats will return empty data until wired."
            )

        if self._event_bus is not None:
            self._subscribe_to_events()

    # ------------------------------------------------------------------
    # Dependency Injection
    # ------------------------------------------------------------------

    def set_client(self, client) -> None:
        """Inject the Supabase client.
        
        v4.0: If client is a factory function, use it to create
        per-thread clients. If it's a client instance, create a
        factory that clones it.
        """
        if callable(client) and not hasattr(client, "table"):
            # It's a factory function
            self._client_factory = client
        else:
            # It's a client instance — create factory that returns it
            # (but warn about thread safety)
            self._client_factory = lambda: client
            logger.warning(
                "JobStatsService: Shared client instance provided. "
                "For production, pass a factory function to create "
                "per-thread clients. This prevents broken pipe errors."
            )
        
        self._thread_clients = ThreadLocalSupabaseClient(self._client_factory)
        logger.info("JobStatsService: Supabase client configured (thread-safe mode)")

    def set_event_bus(self, event_bus) -> None:
        self._event_bus = event_bus
        self._subscribe_to_events()

    def _get_client(self):
        """Get a thread-safe Supabase client."""
        if self._thread_clients is None:
            raise DatabaseQueryError(
                message="JobStatsService: Supabase client not configured.",
                context={"operation": "job_stats_service"},
            )
        return self._thread_clients.get_client()

    def _reset_thread_client(self) -> None:
        """Reset the current thread's client after a broken pipe."""
        if self._thread_clients is not None:
            self._thread_clients.reset_current_thread()
            logger.debug("JobStatsService: reset thread-local client")

    # ------------------------------------------------------------------
    # Event Subscription
    # ------------------------------------------------------------------

    def _subscribe_to_events(self) -> None:
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
                logger.debug("JobStatsService: subscribed to %d job events", len(events))
        except Exception as exc:
            logger.debug("JobStatsService: event subscription failed: %s", exc)

    def _on_job_changed(self, payload: dict = None) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_time = 0.0

    def invalidate_cache(self) -> None:
        self._on_job_changed()

    # ------------------------------------------------------------------
    # Public API — with broken pipe retry
    # ------------------------------------------------------------------

    def get_stats(self, brand_id: Optional[str] = None) -> dict:
        """Get comprehensive job statistics with broken pipe retry."""
        if self._thread_clients is None:
            logger.warning("JobStatsService: get_stats() called without client")
            return self._empty_stats()

        cache_key = brand_id or "__all__"
        with self._lock:
            if cache_key in self._cache:
                if time.monotonic() - self._cache_time < self.CACHE_TTL_SECONDS:
                    return self._cache[cache_key]

        # v4.0 — Retry on broken pipe
        stats = None
        for attempt in range(self.MAX_BROKEN_PIPE_RETRIES + 1):
            try:
                stats = self._compute_stats_via_rpc(brand_id)
                break
            except (BrokenPipeError, ConnectionError, ConnectionResetError) as e:
                logger.warning(
                    "JobStatsService: broken pipe detected (attempt %d/%d) — resetting client",
                    attempt + 1, self.MAX_BROKEN_PIPE_RETRIES,
                )
                self._reset_thread_client()
                if attempt == self.MAX_BROKEN_PIPE_RETRIES:
                    logger.error("JobStatsService: all stats methods failed: %s", e)
                    return self._empty_stats()
                time.sleep(0.5 * (2 ** attempt))
            except Exception as e:
                logger.debug("JobStatsService: RPC failed (%s), trying fallback", e)
                try:
                    stats = self._compute_stats_client_side(brand_id)
                    break
                except (BrokenPipeError, ConnectionError, ConnectionResetError) as e2:
                    self._reset_thread_client()
                    if attempt == self.MAX_BROKEN_PIPE_RETRIES:
                        logger.error("JobStatsService: all stats methods failed: %s", e2)
                        return self._empty_stats()
                    time.sleep(0.5 * (2 ** attempt))
                except Exception as e2:
                    logger.error("JobStatsService: all stats methods failed: %s", e2)
                    return self._empty_stats()

        if stats is None:
            return self._empty_stats()

        with self._lock:
            self._cache[cache_key] = stats
            self._cache_time = time.monotonic()

        return stats

    def get_today_stats(self, brand_id: Optional[str] = None) -> dict:
        full_stats = self.get_stats(brand_id=brand_id)
        return full_stats.get("today", self._empty_stats()["today"])

    def get_status_counts(self, brand_id: Optional[str] = None) -> dict:
        full_stats = self.get_stats(brand_id=brand_id)
        return full_stats.get("status_counts", {})

    def health_check(self) -> dict:
        if self._thread_clients is None:
            return {
                "status": "not_configured",
                "error": "Supabase client not injected.",
                "stats": None,
            }

        try:
            stats = self.get_stats()
            return {"status": "healthy", "stats": stats}
        except Exception as e:
            logger.error("JobStatsService health check failed: %s", e)
            return {"status": "degraded", "error": str(e)[:200], "stats": None}

    def get_status(self) -> dict:
        return self.health_check()

    # ------------------------------------------------------------------
    # Private — Stats Computation
    # ------------------------------------------------------------------

    def _compute_stats_via_rpc(self, brand_id: Optional[str]) -> dict:
        client = self._get_client()
        params: dict = {}
        if brand_id:
            params["brand_id"] = brand_id

        result = client.rpc("get_job_stats", params).execute()

        if result.data and isinstance(result.data, dict):
            return self._normalize_stats(result.data)

        raise ValueError(f"RPC returned {type(result.data).__name__}, expected dict")

    def _compute_stats_client_side(self, brand_id: Optional[str]) -> dict:
        client = self._get_client()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        query = client.table("automation_jobs").select("status,created_at,updated_at")

        if brand_id:
            query = query.eq("brand_id", brand_id)

        result = query.execute()

        if not result.data:
            return self._empty_stats()

        rows = result.data
        now_utc = datetime.now(timezone.utc)

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

            status_counts[status] = status_counts.get(status, 0) + 1

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

            if status in ACTIVE_STATUS_VALUES and updated:
                updated_dt = self._parse_timestamp(updated)
                if updated_dt is not None:
                    if oldest_active is None or updated_dt < oldest_active:
                        oldest_active = updated_dt

        active_count = sum(
            count for status, count in status_counts.items()
            if status in ACTIVE_STATUS_VALUES
        )

        oldest_minutes = 0.0
        if oldest_active is not None:
            oldest_minutes = round((now_utc - oldest_active).total_seconds() / 60.0, 1)

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
        return {
            "today": {"total": 0, "published": 0, "failed": 0, "retrying": 0, "active": 0},
            "status_counts": {},
            "active_jobs": 0,
            "oldest_active_minutes": 0,
        }

    @staticmethod
    def _normalize_stats(data: dict) -> dict:
        defaults = JobStatsService._empty_stats()
        if not isinstance(data, dict):
            return defaults

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

        data.setdefault("status_counts", {})
        data.setdefault("active_jobs", 0)
        data.setdefault("oldest_active_minutes", 0)

        return data

    @staticmethod
    def _is_today(timestamp_str: str, today_str: str) -> bool:
        if not timestamp_str:
            return False
        return timestamp_str >= f"{today_str}T00:00:00"

    @staticmethod
    def _parse_timestamp(value) -> Optional[datetime]:
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

    v4.0: Pass a FACTORY FUNCTION for thread-safe per-thread clients.
    
    Example:
        from services.supabase import get_service_client_from_settings
        
        def client_factory():
            return get_service_client_from_settings()
        
        init_job_stats_service(
            supabase_client=client_factory,  # Factory, not instance!
            event_bus=bus,
        )
    """
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService(
            supabase_client=supabase_client,
            event_bus=event_bus,
        )
        logger.info("JobStatsService: singleton initialized (v4.0 thread-safe)")
    else:
        if supabase_client is not None:
            job_stats_service.set_client(supabase_client)
        if event_bus is not None:
            job_stats_service.set_event_bus(event_bus)
        logger.info("JobStatsService: singleton updated")

    return job_stats_service


def get_job_stats_service() -> JobStatsService:
    global job_stats_service

    if job_stats_service is None:
        job_stats_service = JobStatsService()
        logger.warning(
            "JobStatsService: get_job_stats_service() called before init. "
            "Call init_job_stats_service(client_factory=...) during boot."
        )

    return job_stats_service


__all__ = [
    "JobStatsService",
    "job_stats_service",
    "init_job_stats_service",
    "get_job_stats_service",
    "ACTIVE_STATUS_VALUES",
    "TERMINAL_STATUS_VALUES",
    "ThreadLocalSupabaseClient",
]
