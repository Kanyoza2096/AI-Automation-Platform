"""
Trend Intelligence Admin API v1.1
==================================

Admin endpoints for managing trend sources and brand rules.

v1.1 CHANGES:
  - Added constant-time token comparison
  - Added Pydantic models for input validation
  - Added event publishing for admin actions
  - Enhanced error handling
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
import hmac
import logging

logger = logging.getLogger("kanyoza.plugins.trends.api")

router = APIRouter(prefix="/api/v1/trends/admin", tags=["trends-admin"])


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
# PYDANTIC MODELS (Input Validation)
# ═══════════════════════════════════════════════════════════════════════════

class TrendSourceCreate(BaseModel):
    """Validated model for creating trend sources."""
    name: str = Field(min_length=1, max_length=100)
    platform: str = Field(pattern=r'^(newsapi|reddit|twitter)$')
    query: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    
    @validator('name')
    def sanitize_name(cls, v: str) -> str:
        """Prevent path traversal and injection."""
        v = v.strip()
        if any(char in v for char in ['/', '\\', ';', "'", '"']):
            raise ValueError('Invalid characters in name')
        return v
    
    @validator('query')
    def sanitize_query(cls, v: str) -> str:
        """Sanitize query to prevent injection."""
        v = v.strip()
        if len(v) > 200:
            raise ValueError('Query too long')
        return v


class TrendRuleCreate(BaseModel):
    """Validated model for creating brand rules."""
    brand_id: str = Field(min_length=1, max_length=100)
    keywords: List[str] = Field(min_items=1, max_items=20)
    exclude_keywords: List[str] = Field(default_factory=list, max_items=20)
    min_relevance_score: float = Field(default=60, ge=0, le=100)
    max_posts_per_day: int = Field(default=3, ge=1, le=20)
    enabled: bool = True
    
    @validator('brand_id')
    def validate_brand_id(cls, v: str) -> str:
        """Validate brand ID format."""
        v = v.strip()
        if not v or len(v) > 100:
            raise ValueError('Invalid brand ID')
        return v
    
    @validator('keywords')
    def validate_keywords(cls, v: List[str]) -> List[str]:
        """Sanitize keywords."""
        cleaned = []
        for kw in v:
            kw = kw.strip().lower()
            if kw and len(kw) <= 100:
                cleaned.append(kw)
        if not cleaned:
            raise ValueError('No valid keywords provided')
        return cleaned[:20]  # Limit to 20 keywords
    
    @validator('exclude_keywords')
    def validate_exclude_keywords(cls, v: List[str]) -> List[str]:
        """Sanitize exclude keywords."""
        cleaned = []
        for kw in v:
            kw = kw.strip().lower()
            if kw and len(kw) <= 100:
                cleaned.append(kw)
        return cleaned[:20]  # Limit to 20 keywords


# ═══════════════════════════════════════════════════════════════════════════
# AUTHENTICATION (Constant-time comparison)
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Authenticate admin requests with constant-time comparison."""
    from config.settings import settings
    
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "Missing authentication token")
    
    master_token = settings.master_api_token
    if not master_token:
        logger.error("Master API token is not configured; refusing admin access")
        raise HTTPException(503, "Admin authentication is not configured")
    
    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token.encode('utf-8'), master_token.encode('utf-8')):
        raise HTTPException(401, "Unauthorized")
    
    return True


def _get_supabase():
    """Get Supabase client with proper error handling."""
    try:
        from services.supabase import get_service_client_from_settings
        return get_service_client_from_settings()
    except Exception as exc:
        logger.error("Failed to get Supabase client: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# SOURCES ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/sources")
async def list_sources(_auth=Depends(_require_auth)):
    """List all trend sources."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table("trend_sources").select("*").order("created_at", desc=True).execute()
        return {"sources": result.data or [], "total": len(result.data or [])}
    except Exception as exc:
        logger.error("Failed to list trend sources: %s", exc)
        raise HTTPException(500, "Failed to retrieve sources")


@router.post("/sources")
async def create_source(data: TrendSourceCreate, _auth=Depends(_require_auth)):
    """Create a new trend source."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        # Check for duplicates
        existing = client.table("trend_sources").select("id").eq("platform", data.platform).eq("query", data.query).limit(1).execute()
        if existing.data:
            raise HTTPException(409, "Source already exists")
        
        result = client.table("trend_sources").insert({
            "name": data.name,
            "platform": data.platform,
            "query": data.query,
            "enabled": data.enabled,
            "created_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        }).execute()
        
        # Publish event for new source
        if result.data:
            _publish_event("trend_source_created", {
                "source_id": result.data[0].get("id"),
                "name": data.name,
                "platform": data.platform,
                "query": data.query,
            })
        
        return {"ok": True, "data": result.data[0] if result.data else None}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to create trend source: %s", exc)
        raise HTTPException(500, "Failed to create source")


@router.get("/rules")
async def list_rules(_auth=Depends(_require_auth)):
    """List all brand rules."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        result = client.table("trend_brand_rules").select("*").order("created_at", desc=True).execute()
        return {"rules": result.data or [], "total": len(result.data or [])}
    except Exception as exc:
        logger.error("Failed to list trend rules: %s", exc)
        raise HTTPException(500, "Failed to retrieve rules")


@router.post("/rules")
async def create_rule(data: TrendRuleCreate, _auth=Depends(_require_auth)):
    """Create a new brand rule."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")
    
    try:
        # Check for duplicates
        existing = client.table("trend_brand_rules").select("id").eq("brand_id", data.brand_id).limit(1).execute()
        if existing.data:
            raise HTTPException(409, "Rule already exists for this brand")
        
        result = client.table("trend_brand_rules").insert({
            "brand_id": data.brand_id,
            "keywords": data.keywords,
            "exclude_keywords": data.exclude_keywords,
            "min_relevance_score": data.min_relevance_score,
            "max_posts_per_day": data.max_posts_per_day,
            "enabled": data.enabled,
            "created_at": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        }).execute()
        
        # Publish event for new rule
        if result.data:
            _publish_event("trend_rule_created", {
                "rule_id": result.data[0].get("id"),
                "brand_id": data.brand_id,
                "keywords": data.keywords,
            })
        
        return {"ok": True, "data": result.data[0] if result.data else None}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to create trend rule: %s", exc)
        raise HTTPException(500, "Failed to create rule")


@router.post("/scan")
async def trigger_scan(_auth=Depends(_require_auth)):
    """Trigger an immediate trend scan."""
    try:
        from plugins.trend_intelligence.plugin import trend_plugin
        if not trend_plugin:
            raise HTTPException(503, "Plugin not running")
        
        # Run scan synchronously
        trend_plugin.scan_all_sources()
        trend_plugin.process_trends()
        
        # Publish event for manual scan
        _publish_event("trend_scan_triggered", {
            "triggered_by": "admin_api",
            "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        })
        
        return {
            "ok": True,
            "message": "Scan triggered successfully",
            "timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to trigger scan: %s", exc)
        raise HTTPException(500, f"Scan failed: {str(exc)}")


@router.get("/status")
async def get_status(_auth=Depends(_require_auth)):
    """Get trend intelligence status."""
    try:
        from plugins.trend_intelligence.plugin import trend_plugin
        if not trend_plugin:
            raise HTTPException(503, "Plugin not running")
        
        return {
            "ok": True,
            "running": trend_plugin._running,
            "name": trend_plugin.name,
            "scan_interval_seconds": trend_plugin._scan_interval,
            "published_trends_tracked": len(trend_plugin._published_urls) if hasattr(trend_plugin, '_published_urls') else 0,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get status: %s", exc)
        raise HTTPException(500, "Failed to retrieve status")
