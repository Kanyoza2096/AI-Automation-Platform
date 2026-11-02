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
#   Three handlers in this file were un-scoped (read leak, orphan write,
#   cross-tenant delete). Fixed by routing through KnowledgeRepository and
#   adding require_workspace_ownership checks.
#
# INCIDENT-2026-11-02-knowledge-bulk:
#   require_workspace_ownership was called with 3 args instead of 4; fixed
#   by passing request as the third positional argument everywhere.
#
# INCIDENT-2026-11-02-knowledge-async:
#   KnowledgeBase.add_document / .search / .initialize are `async def`.
#   They were being invoked via _async_wrap (which runs sync callables in a
#   thread pool) — that returns a coroutine object without awaiting it, so
#   `entry.id` failed with 'coroutine' object has no attribute 'id'. Fixed
#   by awaiting the coroutine directly.
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/brands/{brand_id}/knowledge")
async def create_knowledge_entry(
    brand_id: str,
    body: KnowledgeCreateRequest,
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Create a knowledge entry for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
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
        # KnowledgeBase may expose an async initialize(); call it if present.
        init_fn = getattr(kb, "initialize", None)
        if callable(init_fn):
            try:
                init_result = init_fn()
                if asyncio.iscoroutine(init_result):
                    await init_result
            except Exception as e:
                logger.warning(
                    "kb_initialize_failed",
                    extra={
                        "event": "kb_initialize_failed",
                        "brand_id": brand_id,
                        "error_class": type(e).__name__,
                        "error_msg": str(e)[:300],
                    },
                )

        # add_document is async — await directly.
        entry = await kb.add_document(
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
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """List knowledge entries for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
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
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a knowledge entry the caller's workspace owns.

    INCIDENT-2026-10-16-knowledge: previously deleted any entry by ID with
    no ownership check. Now: look up first, verify tenancy binding, delete.
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
            await require_workspace_ownership("brand", brand_id, request, container)
        elif workspace_id:
            await require_workspace_ownership("workspace", workspace_id, request, container)
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
    request: Request,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Search knowledge entries for a brand."""
    try:
        await require_workspace_ownership("brand", brand_id, request, container)
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
        init_fn = getattr(kb, "initialize", None)
        if callable(init_fn):
            try:
                init_result = init_fn()
                if asyncio.iscoroutine(init_result):
                    await init_result
            except Exception as e:
                logger.warning(
                    "kb_initialize_failed",
                    extra={
                        "event": "kb_initialize_failed",
                        "brand_id": brand_id,
                        "error_class": type(e).__name__,
                        "error_msg": str(e)[:300],
                    },
                )

        # search is async — await directly.
        results = await kb.search(query=body.query, top_k=body.top_k)
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
    request: Request,
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Bulk create knowledge entries.

    Each item failure is logged with error_class + error_msg; the first 20
    failures are returned in the response as `error_details` so the caller
    can diagnose without a log round-trip.
    """
    try:
        await require_workspace_ownership("workspace", workspace_id, request, container)
        from repositories.brand_repository import BrandRepository
        from repositories.knowledge_repository import KnowledgeRepository
        from services.knowledge_base import KnowledgeBase

        # The caller owns the workspace, but the requested brand must also
        # belong to that workspace before any records are written.
        brand_repo = await _async_wrap(container.resolve, BrandRepository)
        brand = await _async_wrap(brand_repo.find_by_id, body.brand_id)
        if not brand or brand.workspace_id != workspace_id:
            raise HTTPException(status_code=404, detail="Brand not found")

        kb_repo = await _async_wrap(container.resolve, KnowledgeRepository)
        kb = KnowledgeBase(
            brand_id=body.brand_id,
            workspace_id=workspace_id,
            repository=kb_repo,
        )

        # Initialize the KB if it exposes an async initialize().
        init_fn = getattr(kb, "initialize", None)
        if callable(init_fn):
            try:
                init_result = init_fn()
                if asyncio.iscoroutine(init_result):
                    await init_result
            except Exception as e:
                logger.warning(
                    "bulk_kb_initialize_failed",
                    extra={
                        "event": "bulk_kb_initialize_failed",
                        "error_class": type(e).__name__,
                        "error_msg": str(e)[:300],
                    },
                )

        results, errors = [], []
        for i, item in enumerate(body.items):
            content = _sanitize(str(item.get("content", "")))
            if not content:
                errors.append({"index": i, "error": "content required"})
                continue
            try:
                # add_document is async — await directly, do NOT _async_wrap.
                entry = await kb.add_document(
                    content=content,
                    doc_type=item.get("doc_type", "text"),
                    metadata=item.get("metadata", {}),
                )
                results.append({"id": entry.id, "status": "created"})
            except Exception as e:
                logger.warning(
                    "bulk_item_failed",
                    extra={
                        "event": "bulk_item_failed",
                        "index": i,
                        "brand_id": body.brand_id,
                        "workspace_id": workspace_id,
                        "error_class": type(e).__name__,
                        "error_msg": str(e)[:300],
                    },
                )
                errors.append({
                    "index": i,
                    "error_class": type(e).__name__,
                    "error": str(e)[:300],
                })

        await append_audit(
            audit_log, "knowledge_bulk_created", f"workspace:{workspace_id}",
            status_str="success",
        )
        return _ok({
            "created": len(results),
            "errors": len(errors),
            "error_details": errors[:20],
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

    INCIDENT-2026-10-16-knowledge: previously ran an un-scoped select("*").
    Now scoped to the caller's workspace via KnowledgeRepository.find_by_workspace().
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
    Now: workspace_id is set from the request, failures propagate.
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
