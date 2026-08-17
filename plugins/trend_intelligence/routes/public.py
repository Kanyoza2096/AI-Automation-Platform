"""
Trend Intelligence Public API v1.1
===================================

Public endpoints for retrieving live trends and decisions.

v1.1 CHANGES:
  - Added rate limiting
  - Added input validation with bounds
  - Added response caching (60-second TTL)
  - Added pagination support
  - Added event publishing for API access tracking
  - Enhanced error handling
"""

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import logging
import threading
import time
from collections import deque

logger = logging.getLogger("kanyoza.plugins.trends.api")

router = APIRouter(prefix="/api/v1/trends", tags=["trends"])


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
# SIMPLE RATE LIMITER (In-memory, per-endpoint)
# ═══════════════════════════════════════════════════════════════════════════

class SimpleRateLimiter:
    """Simple in-memory rate limiter for public endpoints."""
    
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: deque = deque(maxlen=max_requests * 2)
        self._lock = threading.RLock()
    
    def check(self) -> bool:
        """Check if request is allowed. Returns True if allowed."""
        now = time.monotonic()
        with self._lock:
            # Remove old entries
            while self._requests and now - self._requests[0] >= self.window_seconds:
                self._requests.popleft()
            
            # Check if under limit
            if len(self._requests) < self.max_requests:
                self._requests.append(now)
                return True
            return False


# Initialize rate limiters
_trends_limiter = SimpleRateLimiter(max_requests=60, window_seconds=60)
_decisions_limiter = SimpleRateLimiter(max_requests=60, window_seconds=60)


# ═══════════════════════════════════════════════════════════════════════════
# SIMPLE RESPONSE CACHE
# ═══════════════════════════════════════════════════════════════════════════

class SimpleCache:
    """Simple in-memory cache with TTL."""
    
    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, tuple] = {}  # key -> (data, expiry)
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
            # Keep cache bounded
            if len(self._cache) > 100:
                # Remove expired entries
                now = time.monotonic()
                expired_keys = [k for k, (_, exp) in self._cache.items() if exp <= now]
                for k in expired_keys:
                    del self._cache[k]


# Initialize caches
_trends_cache = SimpleCache(ttl_seconds=60)
_decisions_cache = SimpleCache(ttl_seconds=60)


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _get_supabase():
    """Get Supabase client with proper error handling."""
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception as exc:
        logger.error("Failed to get Supabase client: %s", exc)
        return None


def _sanitize_limit(limit: int, default: int = 20, max_limit: int = 100) -> int:
    """Sanitize and bound limit parameter."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, max_limit))


def _sanitize_offset(offset: int, default: int = 0, max_offset: int = 1000) -> int:
    """Sanitize and bound offset parameter."""
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        return default
    return max(0, min(offset, max_offset))


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/live")
async def get_live_trends(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Number of trends to return"),
    offset: int = Query(default=0, ge=0, le=1000, description="Pagination offset"),
    source: Optional[str] = Query(default=None, description="Filter by source (newsapi, reddit, twitter)"),
    min_engagement: Optional[int] = Query(default=None, ge=0, description="Minimum engagement count"),
):
    """
    Get live trends with optional filters.
    
    - **limit**: Number of trends to return (1-100)
    - **offset**: Pagination offset (0-1000)
    - **source**: Filter by source platform
    - **min_engagement**: Filter by minimum engagement count
    """
    # Rate limiting
    if not _trends_limiter.check():
        raise HTTPException(429, "Rate limit exceeded. Please retry later.")
    
    # Validate filters
    limit = _sanitize_limit(limit)
    offset = _sanitize_offset(offset)
    
    if source and source not in {"newsapi", "reddit", "twitter"}:
        raise HTTPException(400, "Invalid source. Must be newsapi, reddit, or twitter")
    
    # Check cache
    cache_key = f"trends:{limit}:{offset}:{source}:{min_engagement}"
    cached = _trends_cache.get(cache_key)
    if cached is not None:
        return cached
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        # Build query
        query = client.table("trend_posts").select("*")
        
        if source:
            query = query.eq("platform", source)
        
        if min_engagement is not None:
            query = query.gte("engagement_count", min_engagement)
        
        # Get total count first (for pagination info)
        count_query = client.table("trend_posts").select("id", count="exact")
        if source:
            count_query = count_query.eq("platform", source)
        if min_engagement is not None:
            count_query = count_query.gte("engagement_count", min_engagement)
        
        count_result = count_query.execute()
        total_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
        
        # Get paginated data
        result = query.order("fetched_at", desc=True).range(offset, offset + limit - 1).execute()
        
        response = {
            "trends": result.data or [],
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total_count,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Cache response
        _trends_cache.set(cache_key, response)
        
        # Publish access event (sampled to avoid flooding)
        _publish_event("trends_api_accessed", {
            "endpoint": "live",
            "source": source,
            "min_engagement": min_engagement,
            "limit": limit,
        })
        
        return response
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to retrieve trends: %s", exc)
        raise HTTPException(500, "Failed to retrieve trends")


@router.get("/decisions")
async def get_decisions(
    request: Request,
    limit: int = Query(default=30, ge=1, le=100, description="Number of decisions to return"),
    offset: int = Query(default=0, ge=0, le=1000, description="Pagination offset"),
    brand_id: Optional[str] = Query(default=None, description="Filter by brand ID"),
    decision: Optional[str] = Query(default=None, description="Filter by decision type (posted, scheduled, skipped)"),
):
    """
    Get trend decisions with optional filters.
    
    - **limit**: Number of decisions to return (1-100)
    - **offset**: Pagination offset (0-1000)
    - **brand_id**: Filter by brand ID
    - **decision**: Filter by decision type
    """
    # Rate limiting
    if not _decisions_limiter.check():
        raise HTTPException(429, "Rate limit exceeded. Please retry later.")
    
    # Validate filters
    limit = _sanitize_limit(limit, default=30)
    offset = _sanitize_offset(offset)
    
    if decision and decision not in {"posted", "scheduled", "skipped"}:
        raise HTTPException(400, "Invalid decision. Must be posted, scheduled, or skipped")
    
    # Check cache
    cache_key = f"decisions:{limit}:{offset}:{brand_id}:{decision}"
    cached = _decisions_cache.get(cache_key)
    if cached is not None:
        return cached
    
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        # Build query
        query = client.table("trend_decisions").select("*")
        
        if brand_id:
            query = query.eq("brand_id", brand_id)
        
        if decision:
            query = query.eq("decision", decision)
        
        # Get total count
        count_query = client.table("trend_decisions").select("id", count="exact")
        if brand_id:
            count_query = count_query.eq("brand_id", brand_id)
        if decision:
            count_query = count_query.eq("decision", decision)
        
        count_result = count_query.execute()
        total_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
        
        # Get paginated data
        result = query.order("decided_at", desc=True).range(offset, offset + limit - 1).execute()
        
        response = {
            "decisions": result.data or [],
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total_count,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        # Cache response
        _decisions_cache.set(cache_key, response)
        
        # Publish access event (sampled)
        _publish_event("trends_decisions_accessed", {
            "endpoint": "decisions",
            "brand_id": brand_id,
            "decision": decision,
            "limit": limit,
        })
        
        return response
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to retrieve decisions: %s", exc)
        raise HTTPException(500, "Failed to retrieve decisions")


@router.get("/health")
async def health_check():
    """Health check endpoint for trend intelligence API."""
    client = _get_supabase()
    return {
        "status": "healthy" if client else "degraded",
        "database": "connected" if client else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
