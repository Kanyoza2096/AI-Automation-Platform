from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# WORKSPACES CRUD — A01 Workspace Isolation
#
# INCIDENT-2026-10-16-workspaces:
#   GET /workspaces previously fell through to select("*") on the
#   workspaces table when the caller's workspace could not be resolved,
#   returning every workspace on the platform to any authenticated
#   caller. Now scoped to the caller's memberships via workspace_members.
#
#   The other four handlers were already correct:
#     POST   /workspaces                  — verifies owner matches caller
#     GET    /workspaces/{id}             — require_workspace_ownership
#     PUT    /workspaces/{id}             — ownership + require_workspace_admin
#     DELETE /workspaces/{id}             — ownership + require_workspace_admin
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/workspaces")
async def create_workspace(
    body: WorkspaceCreateRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """A01: Create a new workspace — spec §1.1/§3 contract alignment."""
    try:
        from models.workspace import Workspace
        from repositories.workspace_repository import WorkspaceRepository

        ws_id = body.id or f"ws-{_uuid_mod.uuid4().hex[:12]}"

        # Auto-generate slug if not provided; enforce uniqueness
        slug = body.slug or _sanitize(body.name).lower().replace(" ", "-")
        slug = _re_mod.sub(r"[^a-z0-9\-]", "", slug)[:64]

        repo = await _async_wrap(container.resolve, WorkspaceRepository)

        # Validate unique slug
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                existing = await _async_wrap(
                    lambda: supabase_service.client.table("workspaces")
                    .select("id")
                    .eq("slug", slug)
                    .execute()
                )
                if existing.data:
                    return _err(f"Slug '{slug}' is already in use", 409)
        except Exception:
            pass

        # The authenticated principal is authoritative for workspace ownership.
        # A normal caller cannot create a workspace owned by another identity.
        from core.request_context import require_request_context
        ctx = require_request_context()
        requested_owner = (body.owner or "").strip().lower()
        caller_email = (ctx.user_email or "").strip().lower()
        if ctx.species_privileges:
            owner_email = requested_owner or None
        else:
            if not caller_email:
                raise HTTPException(status_code=403, detail="Workspace creation requires an authenticated user identity")
            if requested_owner and requested_owner != caller_email:
                raise HTTPException(status_code=403, detail="Workspace owner must match the authenticated user")
            owner_email = caller_email

        # Build workspace — only accepted contract fields (spec §1.1)
        ws_kwargs = {
            "id": ws_id,
            "name": body.name,
            "slug": slug,
            "description": body.description,
        }
        if owner_email:
            ws_kwargs["owner_email"] = owner_email
        if body.plan:
            ws_kwargs["plan"] = body.plan

        ws = Workspace(**{k: v for k, v in ws_kwargs.items() if v is not None})
        saved = await _async_wrap(repo.save, ws)

        user_email = auth_user_email_var.get()
        await append_audit(
            audit_log, "workspace_created", f"workspace:{ws_id}",
            user=user_email or "system", status_str="success",
        )
        return _ok({"workspace": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/workspaces")


@router.get("/workspaces")
async def list_workspaces(
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A01: List workspaces the caller is a member of.

    Scoped to the caller's memberships via workspace_members.user_id,
    which stores the Supabase auth user id (same value as
    RequestContext.principal_id — see species_auth.py:155).

    Only accepted memberships are returned. Pending invitations
    (accepted_at IS NULL) do not grant visibility.

    INCIDENT-2026-10-16-workspaces: the previous implementation fell
    through to select("*") on the workspaces table when the caller's
    workspace could not be resolved, returning every workspace on the
    platform to any authenticated caller. That fallback is removed.
    """
    try:
        from core.request_context import require_request_context
        ctx = require_request_context()
        caller_user_id = (ctx.principal_id or "").strip()
        if not caller_user_id or caller_user_id in ("anonymous", "species"):
            raise HTTPException(status_code=403, detail="Authenticated identity is required")

        from services.supabase import supabase_service
        if not (supabase_service and supabase_service.client):
            # Dev fallback — session-bound workspace only. No global listing.
            workspace_id = _resolve_workspace_from_request(request)
            if not workspace_id:
                return _ok({"workspaces": []})
            from repositories.workspace_repository import WorkspaceRepository
            repo = await _async_wrap(container.resolve, WorkspaceRepository)
            ws = await _async_wrap(repo.find_by_id, workspace_id)
            return _ok({"workspaces": [ws.model_dump(mode="json")] if ws else []})

        # 1. Fetch the caller's memberships.
        memberships = await _async_wrap(
            lambda: supabase_service.client.table("workspace_members")
            .select("workspace_id, role, accepted_at")
            .eq("user_id", caller_user_id)
            .execute()
        )
        # Deduplicate and keep only accepted memberships. Pending invitations
        # (accepted_at IS NULL) do not confer visibility.
        member_ws_ids = sorted({
            r["workspace_id"]
            for r in (memberships.data or [])
            if r.get("workspace_id") and r.get("accepted_at") is not None
        })

        if not member_ws_ids:
            return _ok({"workspaces": []})

        # 2. Fetch the workspace rows for those IDs.
        result = await _async_wrap(
            lambda: supabase_service.client.table("workspaces")
            .select("*")
            .in_("id", member_ws_ids)
            .execute()
        )
        return _ok({"workspaces": result.data or []})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workspaces")


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """A01: Get a workspace — ownership verified."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.workspace_repository import WorkspaceRepository
        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        ws = await _async_wrap(repo.find_by_id, workspace_id)
        if not ws:
            return _err("Workspace not found", 404)
        return _ok({"workspace": ws.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}")


@router.put("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    body: dict,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """A01: Update a workspace — ownership verified."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from models.workspace import Workspace
        from repositories.workspace_repository import WorkspaceRepository

        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        ws = await _async_wrap(repo.find_by_id, workspace_id)
        if not ws:
            return _err("Workspace not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        ws_dict = ws.model_dump(mode="json")
        ws_dict.update(update_data)

        saved = await _async_wrap(repo.save, Workspace(**ws_dict))
        await append_audit(
            audit_log, "workspace_updated", f"workspace:{workspace_id}", status_str="success",
        )
        return _ok({"workspace": saved.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/workspaces/{workspace_id}")


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """A01: Delete a workspace — ownership verified."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.workspace_repository import WorkspaceRepository
        repo = await _async_wrap(container.resolve, WorkspaceRepository)
        deleted = await _async_wrap(repo.delete, workspace_id)
        await append_audit(
            audit_log, "workspace_deleted", f"workspace:{workspace_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/workspaces/{workspace_id}")
