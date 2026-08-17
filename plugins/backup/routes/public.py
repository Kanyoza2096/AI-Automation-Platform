"""
Backup Plugin Public Routes v2.1
=================================

v2.1 CHANGES:
  - Added rate limiting for public endpoints
  - Added response caching (30-second TTL)
  - Added input validation for filters
  - Added event publishing for access tracking
  - Enhanced error handling with logging
"""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import logging
import threading
import time
from collections import deque

logger = logging.getLogger("kanyoza.plugins.backup.api")

router = APIRouter(
    prefix="/api/v1/backup", tags=["backup"]
)


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Safely publish event to shared bus."""
    if not HAS_EVENT_BUS:
        return
    try:
        bus = get_shared_event_bus()
        bus.publish(event_name, payload)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# SIMPLE RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════

class SimpleRateLimiter:
    """Simple in-memory rate limiter for public endpoints."""
    
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: deque = deque(maxlen=max_requests * 2)
        self._lock = threading.RLock()
    
    def check(self) -> bool:
        """Check if request is allowed."""
        now = time.monotonic()
        with self._lock:
            while self._requests and now - self._requests[0] >= self.window_seconds:
                self._requests.popleft()
            
            if len(self._requests) < self.max_requests:
                self._requests.append(now)
                return True
            return False


# Initialize rate limiters
_status_limiter = SimpleRateLimiter(max_requests=30, window_seconds=60)
_records_limiter = SimpleRateLimiter(max_requests=30, window_seconds=60)
_quick_limiter = SimpleRateLimiter(max_requests=60, window_seconds=60)


# ═══════════════════════════════════════════════════════════════════════════
# SIMPLE RESPONSE CACHE
# ═══════════════════════════════════════════════════════════════════════════

class SimpleCache:
    """Simple in-memory cache with TTL."""
    
    def __init__(self, ttl_seconds: int = 30):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        with self._lock:
            if key in self._cache:
                data, expiry = self._cache[key]
                if time.monotonic() < expiry:
                    return data
                del self._cache[key]
            return None
    
    def set(self, key: str, value: Any) -> None:
        """Set cache value with TTL."""
        with self._lock:
            self._cache[key] = (value, time.monotonic() + self.ttl_seconds)
            # Cleanup expired entries
            if len(self._cache) > 50:
                now = time.monotonic()
                expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
                for k in expired:
                    del self._cache[k]


# Initialize caches
_status_cache = SimpleCache(ttl_seconds=30)
_records_cache = SimpleCache(ttl_seconds=30)
_quick_cache = SimpleCache(ttl_seconds=15)


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _get_supabase():
    """Get Supabase client with error handling."""
    try:
        from services.supabase import (
            get_service_client_from_settings,
        )
        return get_service_client_from_settings()
    except Exception as exc:
        logger.error("Failed to get Supabase client: %s", exc)
        return None


def _get_backup_plugin():
    """Get the running backup plugin instance."""
    try:
        from plugins.backup.plugin import (
            backup_plugin,
        )
        return backup_plugin
    except Exception as exc:
        logger.error("Failed to get backup plugin: %s", exc)
        return None


def _validate_status(status: Optional[str]) -> Optional[str]:
    """Validate status parameter."""
    if status is None:
        return None
    allowed = {"running", "completed", "failed"}
    if status not in allowed:
        raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(allowed))}")
    return status


def _sanitize_limit(limit: int, default: int = 20, max_limit: int = 100) -> int:
    """Sanitize and bound limit parameter."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, max_limit))


# ═══════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/status")
async def get_backup_status(request: Request):
    """Get backup status for all targets."""
    # Rate limiting
    if not _status_limiter.check():
        raise HTTPException(429, "Rate limit exceeded. Please retry later.")
    
    # Check cache
    cached = _status_cache.get("status")
    if cached is not None:
        return cached
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    try:
        plugin = _get_backup_plugin()
        targets = (
            client.table("backup_targets")
            .select("*")
            .order("name")
            .execute()
        )

        result = []
        for t in targets.data or []:
            last = (
                client.table("backup_records")
                .select("*")
                .eq("target_id", t["id"])
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )

            last_record = (
                last.data[0] if last.data else None
            )

            result.append({
                "name": t.get("name", "Unknown"),
                "enabled": t.get("enabled", False),
                "schedule": t.get("schedule", "daily"),
                "retention_days": t.get(
                    "retention_days", 30
                ),
                "last_backup": last_record,
                "last_backup_status": (
                    last_record.get("status", "never")
                    if last_record
                    else "never"
                ),
            })

        response = {
            "targets": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Include plugin stats if available
        if plugin:
            response["stats"] = plugin.get_stats()

        # Cache response
        _status_cache.set("status", response)
        
        # Publish access event (sampled)
        _publish_event("backup_status_accessed", {
            "endpoint": "status",
            "targets_count": len(result),
        })

        return response
    except Exception as exc:
        logger.error("Failed to get backup status: %s", exc)
        raise HTTPException(500, "Failed to retrieve backup status")


# ═══════════════════════════════════════════════════════════════════════════
# RECORDS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/records")
async def get_backup_records(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Number of records to return"),
    offset: int = Query(default=0, ge=0, le=1000, description="Pagination offset"),
    target_id: Optional[str] = Query(default=None, description="Filter by target ID"),
    status: Optional[str] = Query(default=None, description="Filter by status (running, completed, failed)"),
):
    """Get backup records with optional filters and pagination."""
    # Rate limiting
    if not _records_limiter.check():
        raise HTTPException(429, "Rate limit exceeded. Please retry later.")
    
    # Validate status
    status = _validate_status(status)
    limit = _sanitize_limit(limit, default=20, max_limit=100)
    
    # Check cache
    cache_key = f"records:{limit}:{offset}:{target_id}:{status}"
    cached = _records_cache.get(cache_key)
    if cached is not None:
        return cached
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    try:
        # Build query
        query = client.table("backup_records").select("*")
        
        if target_id:
            query = query.eq("target_id", target_id)
        
        if status:
            query = query.eq("status", status)
        
        # Get total count
        count_query = client.table("backup_records").select("id", count="exact")
        if target_id:
            count_query = count_query.eq("target_id", target_id)
        if status:
            count_query = count_query.eq("status", status)
        
        count_result = count_query.execute()
        total = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
        
        # Get paginated data
        result = query.order("started_at", desc=True).range(offset, offset + limit - 1).execute()
        
        response = {
            "records": result.data or [],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Cache response
        _records_cache.set(cache_key, response)
        
        # Publish access event
        _publish_event("backup_records_accessed", {
            "endpoint": "records",
            "target_id": target_id,
            "status": status,
            "limit": limit,
        })
        
        return response
    except Exception as exc:
        logger.error("Failed to get backup records: %s", exc)
        raise HTTPException(500, "Failed to retrieve backup records")


# ═══════════════════════════════════════════════════════════════════════════
# v2.0 — QUICK STATUS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/quick")
async def get_quick_status(request: Request):
    """Lightweight backup status — for dashboards and health checks."""
    # Rate limiting (more lenient for health checks)
    if not _quick_limiter.check():
        raise HTTPException(429, "Rate limit exceeded. Please retry later.")
    
    # Check cache (shorter TTL for health checks)
    cached = _quick_cache.get("quick")
    if cached is not None:
        return cached
    
    plugin = _get_backup_plugin()

    if not plugin:
        response = {
            "status": "unknown",
            "message": "Backup plugin not running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _quick_cache.set("quick", response)
        return response

    stats = plugin.get_stats()

    # Determine status
    if (
        stats.get("backups_failed", 0) == 0
        and stats.get("backups_completed", 0) > 0
    ):
        status = "healthy"
    elif stats.get("backups_completed", 0) == 0:
        status = "no_backups_yet"
    elif stats.get("backups_failed", 0) > 0:
        status = "has_failures"
    else:
        status = "unknown"

    response = {
        "status": status,
        "stats": stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Cache response
    _quick_cache.set("quick", response)

    return response


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/health")
async def health_check():
    """Health check endpoint — no rate limiting."""
    plugin = _get_backup_plugin()
    client = _get_supabase()
    
    return {
        "status": "healthy" if (plugin and client) else "degraded",
        "plugin": "running" if plugin else "stopped",
        "database": "connected" if client else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
