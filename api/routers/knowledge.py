from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE
#
# INCIDENT-2026-10-16-knowledge:
#   Three handlers in this file were un-scoped:
#     * GET /knowledge           — read leak: no workspace filter, returned
#                                  every tenant's knowledge entries to any
#                                  authenticated user.
#     * POST /knowledge          — orphan write: no workspace_id on the
#                                  inserted row; silent failure swallowed.
#     * DELETE /knowledge/{id}   — cross-tenant delete: no ownership check;
#                                  any authenticated user could delete any
#                                  entry by ID.
#
#   Fixed by routing through KnowledgeRepository (which already had
#   find_by_workspace / find_by_id) and adding require_workspace_ownership
#   checks on the destructive path.
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/brands/{brand_id}/knowledge")
async def create_knowledge_entry(
    brand_id: str,
    body: KnowledgeCreateRequest,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Create a knowledge entry for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.brand_repository import BrandRepository
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=brand_id,
            workspace_id=brand.workspace_id,
            repository=kb_repo,
        )
        entry = await _async_wrap(
            kb.add_document,
            content=body.content,
            doc_type=body.doc_type,
            metadata=body.metadata,
        )
        return _ok({"knowledge_entry": entry.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/brands/{brand_id}/knowledge")


@router.get("/brands/{brand_id}/knowledge")
async def list_knowledge_entries(
    brand_id: str,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List knowledge entries for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(container.resolve, KnowledgeRepository)
        entries = await _async_wrap(repo.find_by_brand, brand_id)
        return _ok({"knowledge_entries": [e.model_dump(mode="json") for e in entries]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET /api/v1/brands/{brand_id}/knowledge")


@router.delete("/knowledge/{knowledge_id}")
async def delete_knowledge_entry(
    knowledge_id: str,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a knowledge entry the caller's workspace owns.

    INCIDENT-2026-10-16-knowledge: previously deleted any entry by ID
    with no ownership check. Any authenticated user could destroy another
    tenant's knowledge base. Now: look up first, verify the caller's
    workspace owns the entry (via brand or workspace), then delete.
    """
    try:
        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(container.resolve, KnowledgeRepository)

        entry = await _async_wrap(repo.find_by_id, knowledge_id)
        if entry is None:
            return _err("Knowledge entry not found", 404)

        brand_id = getattr(entry, "brand_id", None)
        workspace_id = getattr(entry, "workspace_id", None)
        if brand_id:
            await require_workspace_ownership("brand", brand_id, container)
        elif workspace_id:
            await require_workspace_ownership("workspace", workspace_id, container)
        else:
            # No tenancy binding on the row. Refuse rather than allow a
            # delete against an ambiguous target.
            raise HTTPException(status_code=404, detail="Knowledge entry not found")

        deleted = await _async_wrap(repo.delete, knowledge_id)
        await append_audit(
            audit_log, "knowledge_deleted", f"knowledge:{knowledge_id}", status_str="success",
        )
        return _ok({"deleted": deleted})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"DELETE /api/v1/knowledge/{knowledge_id}")


@router.post("/brands/{brand_id}/knowledge/search")
async def search_knowledge_entries(
    brand_id: str,
    body: KnowledgeSearchRequest,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Search knowledge entries for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, container)
        from repositories.brand_repository import BrandRepository
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, brand_id)
        if not brand:
            return _err("Brand not found", 404)

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=brand_id,
            workspace_id=brand.workspace_id,
            repository=kb_repo,
        )
        results = await _async_wrap(kb.search, query=body.query, top_k=body.top_k)
        serialised = [
            {"entry": entry.model_dump(mode="json"), "similarity": float(sim)}
            for entry, sim in results
        ]
        return _ok({"results": serialised})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/brands/{brand_id}/knowledge/search")


@router.post("/workspaces/{workspace_id}/knowledge/bulk")
async def bulk_create_knowledge(
    workspace_id: str,
    body: BulkKnowledgeRequest,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Bulk create knowledge entries."""
    try:
        await require_workspace_ownership("workspace", workspace_id, container)
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=body.brand_id,
            workspace_id=workspace_id,
            repository=kb_repo,
        )

        results, errors = [], []
        for i, item in enumerate(body.items):
            content = _sanitize(str(item.get("content", "")))
            if not content:
                errors.append({"index": i, "error": "content required"})
                continue
            try:
                entry = await _async_wrap(
                    kb.add_document,
                    content=content,
                    doc_type=item.get("doc_type", "text"),
                    metadata=item.get("metadata", {}),
                )
                results.append({"id": entry.id, "status": "created"})
            except Exception as e:
                errors.append({"index": i, "error": str(e)[:200]})

        await append_audit(
            audit_log, "knowledge_bulk_created", f"workspace:{workspace_id}",
            status_str="success",
        )
        return _ok({
            "created": len(results),
            "errors": len(errors),
            "items": results,
        }, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST /api/v1/workspaces/{workspace_id}/knowledge/bulk")


@router.get("/knowledge")
async def list_knowledge(
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    limit: int = 50,
    q: str = "",
):
    """List knowledge entries for the caller's workspace.

    INCIDENT-2026-10-16-knowledge: previously ran an un-scoped
    select("*") against knowledge_entries via the service-role client,
    returning every workspace's entries to any authenticated user.
    Now scoped to the caller's workspace via
    KnowledgeRepository.find_by_workspace().
    """
    try:
        limit = min(200, max(1, limit))
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(container.resolve, KnowledgeRepository)
        entries = await _async_wrap(repo.find_by_workspace, workspace_id)

        docs = [e.model_dump(mode="json") for e in (entries or [])]
        search = q.strip().lower()
        if search:
            docs = [d for d in docs if search in (d.get("content") or "").lower()]
        docs = docs[:limit]

        return _ok({"documents": docs, "count": len(docs), "source": "repository"})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/knowledge")


@router.post("/knowledge")
async def create_knowledge(
    body: KnowledgeCreateRequest,
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a knowledge entry in the caller's workspace.

    INCIDENT-2026-10-16-knowledge: previously inserted a row with no
    workspace_id (orphan) and swallowed write failures with bare except.
    Now: workspace_id is set from the request, the write goes through
    KnowledgeRepository.save(), and failures propagate.
    """
    try:
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        from repositories.knowledge_repository import KnowledgeRepository
        from models.knowledge_entry import KnowledgeEntry

        entry = KnowledgeEntry(
            id=_uuid_mod.uuid4().hex,
            workspace_id=workspace_id,
            title=body.title or body.content[:80],
            content=body.content,
            doc_type=body.doc_type,
        )

        repo = await _async_wrap(container.resolve, KnowledgeRepository)
        saved = await _async_wrap(repo.save, entry)

        await append_audit(
            audit_log, "knowledge_created", f"knowledge:{saved.id}", status_str="success",
        )
        return _ok({"document": saved.model_dump(mode="json")}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/knowledge")
