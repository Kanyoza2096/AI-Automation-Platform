from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# BRANDS CRUD
# ═══════════════════════════════════════════════════════════════════════════

// Authenticated current-workspace lookup for operator consoles that collect credentials
// after serving a static UI shell. Workspace identity comes only from require_auth.
@router.get("/workspaces/current/brands")
async def list_current_workspace_brands(
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List brands for the workspace bound to the authenticated credentials."""
    try:
        workspace_id = auth_workspace_id_var.get()
        if not workspace_id:
            raise HTTPException(status_code=403, detail="No workspace bound to credentials")
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        brands = await _async_wrap(repo.find_by_workspace, workspace_id)
        return _ok({"brands": [b.model_dump(mode="json") for b in brands]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/workspaces/current/brands")


@router.post("/workspaces/{workspace_id}/brands")
async def create_brand(
    workspace_id: str,
    body: dict,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a brand in a workspace."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from models.brand import Brand
        from repositories.brand_repository import BrandRepository

        brand_id = body.get("id") or f"brand-{_uuid_mod.uuid4().hex[:12]}"
        body["id"] = brand_id
        body["workspace_id"] = workspace_id
        if not body.get("name"):
            return _err("name is required", 400)
        if not body.get("slug"):
            body["slug"] = _sanitize(body["name"]).lower().replace(" ", "-")

        brand = Brand(**body)
        repo = await _async_wrap(container.resolve, BrandRepository)
        saved = await _async_wrap(repo.save, brand)

        await append_audit(
            audit_log, "brand_created", f"brand:{brand_id}", status_str="success",
        )
        return _ok({"brand": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/brands")


@router.get("/workspaces/{workspace_id}/brands")
async def list_brands(
    workspace_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List brands in a workspace."""
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        brands = await _async_wrap(repo.find_by_workspace, workspace_id)
        return _ok({"brands": [b.model_dump(mode="json") for b in brands]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/workspaces/{workspace_id}/brands")


@router.get("/brands/{brand_id}")
async def get_brand(
    brand_id: str,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Get a specific brand."""
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)
        return _ok({"brand": brand.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}")


@router.put("/brands/{brand_id}")
async def update_brand(
    brand_id: str,
    body: dict,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Update a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
        from models.brand import Brand
        from repositories.brand_repository import BrandRepository

        repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)

        update_data = {
            k: v for k, v in body.items()
            if k not in ("id", "workspace_id", "created_at")
        }
        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        brand_dict = brand.model_dump(mode="json")
        brand_dict.update(update_data)

        saved = await _async_wrap(repo.save, Brand(**brand_dict))
        await append_audit(
            audit_log, "brand_updated", f"brand:{brand_id}", status_str="success",
        )
        return _ok({"brand": saved.model_dump(mode="json")})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"PUT /api/v1/brands/{brand_id}")


@router.delete("/brands/{brand_id}")
async def delete_brand(
    brand_id: str,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
        from repositories.brand_repository import BrandRepository
        repo = await _async_wrap(container.resolve, BrandRepository)
        deleted = await _async_wrap(repo.delete, brand_id)
        await append_audit(
            audit_log, "brand_deleted", f"brand:{brand_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/brands/{brand_id}")
