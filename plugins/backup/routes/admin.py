"""
Backup Plugin Admin Routes v2.1
================================

v2.1 CHANGES:
  - Added constant-time token comparison
  - Added Pydantic models for input validation
  - Added event publishing for backup actions
  - Enhanced error handling with logging
  - Added pagination for targets list
"""

from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    Request,
    Query,
)
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
import hmac
import logging
from datetime import datetime, timezone

logger = logging.getLogger("kanyoza.plugins.backup.api")

router = APIRouter(
    prefix="/api/v1/backup/admin",
    tags=["backup-admin"],
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
# PYDANTIC MODELS (Input Validation)
# ═══════════════════════════════════════════════════════════════════════════

class BackupTargetCreate(BaseModel):
    """Validated model for creating backup targets."""
    name: str = Field(min_length=1, max_length=100)
    connection_string_encrypted: str = Field(min_length=1, max_length=5000)
    retention_days: int = Field(default=30, ge=1, le=365)
    enabled: bool = True
    
    @validator('name')
    def sanitize_name(cls, v: str) -> str:
        """Sanitize name to prevent injection."""
        v = v.strip()
        if any(char in v for char in ['/', '\\', ';', "'", '"', '`']):
            raise ValueError('Invalid characters in name')
        return v


class BackupTargetUpdate(BaseModel):
    """Validated model for updating backup targets."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    connection_string_encrypted: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    retention_days: Optional[int] = Field(default=None, ge=1, le=365)
    enabled: Optional[bool] = None
    
    @validator('name')
    def sanitize_name(cls, v: Optional[str]) -> Optional[str]:
        """Sanitize name to prevent injection."""
        if v is None:
            return None
        v = v.strip()
        if any(char in v for char in ['/', '\\', ';', "'", '"', '`']):
            raise ValueError('Invalid characters in name')
        return v


# ═══════════════════════════════════════════════════════════════════════════
# AUTH (Constant-time comparison)
# ═══════════════════════════════════════════════════════════════════════════

def _require_auth(request: Request):
    """Authenticate with constant-time token comparison."""
    from config.settings import settings

    token = request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")
    
    if not token:
        raise HTTPException(401, "Missing authentication token")
    
    master_token = settings.master_api_token
    if not master_token:
        logger.warning("Master API token not configured — rejecting request")
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="MASTER_API_TOKEN not configured")
    
    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token.encode('utf-8'), master_token.encode('utf-8')):
        raise HTTPException(401, "Unauthorized")
    
    return True


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _get_supabase():
    """Get Supabase client with proper error handling."""
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


# ═══════════════════════════════════════════════════════════════════════════
# TARGET CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/targets")
async def list_targets(
    _auth=Depends(_require_auth),
    limit: int = Query(default=100, ge=1, le=500, description="Number of targets to return"),
    offset: int = Query(default=0, ge=0, le=1000, description="Pagination offset"),
):
    """List all backup targets with pagination."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    try:
        # Get total count
        count_result = client.table("backup_targets").select("id", count="exact").execute()
        total = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
        
        # Get paginated data
        result = (
            client.table("backup_targets")
            .select("*")
            .order("name")
            .range(offset, offset + limit - 1)
            .execute()
        )
        
        return {
            "targets": result.data or [],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }
    except Exception as exc:
        logger.error("Failed to list backup targets: %s", exc)
        raise HTTPException(500, "Failed to retrieve targets")


@router.post("/targets")
async def create_target(
    data: BackupTargetCreate,
    _auth=Depends(_require_auth),
):
    """Create a new backup target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    try:
        # Check for duplicate names
        existing = client.table("backup_targets").select("id").eq("name", data.name).limit(1).execute()
        if existing.data:
            raise HTTPException(409, f"Target '{data.name}' already exists")
        
        result = (
            client.table("backup_targets")
            .insert({
                "name": data.name,
                "connection_string_encrypted": data.connection_string_encrypted,
                "retention_days": data.retention_days,
                "enabled": data.enabled,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            .execute()
        )
        
        # Publish event for new target
        if result.data:
            _publish_event("backup_target_created", {
                "target_id": result.data[0].get("id"),
                "name": data.name,
                "retention_days": data.retention_days,
            })
        
        return {
            "ok": True,
            "data": result.data[0] if result.data else None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to create backup target: %s", exc)
        raise HTTPException(500, "Failed to create target")


@router.put("/targets/{target_id}")
async def update_target(
    target_id: str,
    data: BackupTargetUpdate,
    _auth=Depends(_require_auth),
):
    """Update a backup target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    # Check if target exists
    existing = client.table("backup_targets").select("id").eq("id", target_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Target not found")

    # Build update payload (only include non-None fields)
    update_data = {k: v for k, v in data.dict().items() if v is not None}
    
    if not update_data:
        raise HTTPException(400, "No fields to update")
    
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        client.table("backup_targets").update(
            update_data
        ).eq("id", target_id).execute()
        
        # Publish event for update
        _publish_event("backup_target_updated", {
            "target_id": target_id,
            "updated_fields": list(update_data.keys()),
        })
        
        return {"ok": True, "updated_fields": list(update_data.keys())}
    except Exception as exc:
        logger.error("Failed to update backup target: %s", exc)
        raise HTTPException(500, "Failed to update target")


@router.delete("/targets/{target_id}")
async def delete_target(
    target_id: str,
    _auth=Depends(_require_auth),
):
    """Delete a backup target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    # Check if target exists
    existing = client.table("backup_targets").select("id,name").eq("id", target_id).limit(1).execute()
    if not existing.data:
        raise HTTPException(404, "Target not found")

    try:
        client.table("backup_targets").delete().eq(
            "id", target_id
        ).execute()
        
        # Publish event for deletion
        _publish_event("backup_target_deleted", {
            "target_id": target_id,
            "name": existing.data[0].get("name", ""),
        })
        
        return {"ok": True}
    except Exception as exc:
        logger.error("Failed to delete backup target: %s", exc)
        raise HTTPException(500, "Failed to delete target")


# ═══════════════════════════════════════════════════════════════════════════
# ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/run")
async def run_backup_now(
    _auth=Depends(_require_auth),
):
    """Trigger an immediate backup for all enabled targets."""
    plugin = _get_backup_plugin()
    if not plugin:
        raise HTTPException(503, "Backup plugin not running")

    try:
        plugin._handle_run({})
        stats = plugin.get_stats()
        
        # Publish event for backup trigger
        _publish_event("backup_triggered", {
            "triggered_by": "admin_api",
            "scope": "all_targets",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        return {
            "ok": True,
            "message": "Backup triggered",
            "stats": stats,
        }
    except Exception as exc:
        logger.error("Failed to trigger backup: %s", exc)
        raise HTTPException(500, str(exc))


@router.post("/run/{target_id}")
async def run_backup_for_target(
    target_id: str,
    _auth=Depends(_require_auth),
):
    """Trigger a backup for a specific target."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    # Find the target
    target = (
        client.table("backup_targets")
        .select("*")
        .eq("id", target_id)
        .limit(1)
        .execute()
    )

    if not target.data:
        raise HTTPException(404, "Target not found")

    plugin = _get_backup_plugin()
    if not plugin:
        raise HTTPException(503, "Backup plugin not running")

    try:
        plugin._run_backup(target.data[0])
        
        # Publish event for specific target backup
        _publish_event("backup_triggered", {
            "triggered_by": "admin_api",
            "scope": "single_target",
            "target_id": target_id,
            "target_name": target.data[0].get("name", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        return {
            "ok": True,
            "message": f"Backup triggered for {target.data[0].get('name', target_id)}",
        }
    except Exception as exc:
        logger.error("Failed to trigger backup for target %s: %s", target_id, exc)
        raise HTTPException(500, str(exc))


# ═══════════════════════════════════════════════════════════════════════════
# v2.0 — STATS & HEALTH
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/stats")
async def get_stats(
    _auth=Depends(_require_auth),
):
    """Get backup plugin statistics."""
    plugin = _get_backup_plugin()
    if not plugin:
        raise HTTPException(503, "Backup plugin not running")

    return {
        "ok": True,
        "stats": plugin.get_stats(),
    }


@router.get("/health")
async def get_health(
    _auth=Depends(_require_auth),
):
    """Get backup plugin health."""
    plugin = _get_backup_plugin()
    if not plugin:
        return {
            "ok": True,
            "health": {
                "status": "stopped",
                "stats": {},
            },
        }

    health = plugin.health_check()
    
    return {
        "ok": True,
        "health": health,
    }


@router.get("/records")
async def list_records(
    _auth=Depends(_require_auth),
    limit: int = Query(default=50, ge=1, le=200, description="Number of records to return"),
    offset: int = Query(default=0, ge=0, le=1000, description="Pagination offset"),
    status: Optional[str] = Query(default=None, description="Filter by status (running, completed, failed)"),
):
    """List backup records with pagination and filtering."""
    client = _get_supabase()
    if not client:
        raise HTTPException(503, "Database unavailable")

    if status and status not in {"running", "completed", "failed"}:
        raise HTTPException(400, "Invalid status. Must be running, completed, or failed")

    try:
        # Build query
        query = client.table("backup_records").select("*")
        
        if status:
            query = query.eq("status", status)
        
        # Get total count
        count_query = client.table("backup_records").select("id", count="exact")
        if status:
            count_query = count_query.eq("status", status)
        
        count_result = count_query.execute()
        total = count_result.count if hasattr(count_result, 'count') else len(count_result.data or [])
        
        # Get paginated data
        result = query.order("started_at", desc=True).range(offset, offset + limit - 1).execute()
        
        return {
            "records": result.data or [],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }
    except Exception as exc:
        logger.error("Failed to list backup records: %s", exc)
        raise HTTPException(500, "Failed to retrieve records")
