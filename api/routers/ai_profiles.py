from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# AI PROFILES CRUD
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/workspaces/{workspace_id}/ai-profiles")
async def create_ai_profile(
    request: Request,
    workspace_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create an AI profile."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request)
        from models.ai_profile import AIProfile
        from repositories.ai_profile_repository import AIProfileRepository

        profile_id = body.get("id") or f"KDO_AI_{_uuid_mod.uuid4().hex}"
        body["id"] = profile_id
        body["workspace_id"] = workspace_id
        if not body.get("name"):
            return _err("name is required", 400)
        profile = AIProfile(**body)
        repo = await _async_wrap(container.resolve, AIProfileRepository)
        saved = await _async_wrap(repo.save, profile)

        await append_audit(
            audit_log,
            "ai_profile_created",
            f"ai_profile:{profile_id}",
            status_str="success",
        )
        return _ok({"ai_profile": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/ai-profiles")


@router.get("/workspaces/{workspace_id}/ai-profiles")
async def list_ai_profiles(
    request: Request,
    workspace_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List AI profiles."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request)
        from repositories.ai_profile_repository import AIProfileRepository
        repo = await _async_wrap(container.resolve, AIProfileRepository)
        profiles = await _async_wrap(repo.find_by_workspace, workspace_id)
        return _ok({"ai_profiles": [p.model_dump(mode="json") for p in profiles]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/ai-profiles")


@router.put("/ai-profiles/{profile_id}")
async def update_ai_profile(
    request: Request,
    profile_id: str,
    body: dict,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Update an AI profile."""
    try:
        await require_workspace_ownership("ai_profile", profile_id, request)
        from models.ai_profile import AIProfile
        from repositories.ai_profile_repository import AIProfileRepository
        # ... rest of the update logic ...
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/ai-profiles/{profile_id}")
