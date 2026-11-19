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
        # A failed index load must fail the request; never search or write
        # against an uninitialized, potentially empty index.
        try:
            await kb.initialize()
        except Exception as e:
            logger.error(
                "kb_initialize_failed",
                extra={
                    "event": "kb_initialize_failed",
                    "brand_id": brand_id,
                    "error_class": type(e).__name__,
                    "error_msg": str(e)[:300],
                },
            )
            raise HTTPException(status_code=503, detail="Knowledge index unavailable") from e

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
        # A failed index load must fail the request; never search or write
        # against an uninitialized, potentially empty index.
        try:
            await kb.initialize()
        except Exception as e:
            logger.error(
                "kb_initialize_failed",
                extra={
                    "event": "kb_initialize_failed",
                    "brand_id": brand_id,
                    "error_class": type(e).__name__,
                    "error_msg": str(e)[:300],
                },
            )
            raise HTTPException(status_code=503, detail="Knowledge index unavailable") from e

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

        # A failed index load must abort bulk ingestion before any writes.
        try:
            await kb.initialize()
        except Exception as e:
            logger.error(
                "bulk_kb_initialize_failed",
                extra={
                    "event": "bulk_kb_initialize_failed",
                    "brand_id": body.brand_id,
                    "workspace_id": workspace_id,
                    "error_class": type(e).__name__,
                    "error_msg": str(e)[:300],
                },
            )
            raise HTTPException(status_code=503, detail="Knowledge index unavailable") from e

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


# ── Governed evidence review API ────────────────────────────────────────────
# These endpoints govern reusable knowledge-source trust, NOT individual posts.
# Review approval never mutates knowledge_entries.metadata.verified; promotion
# is a separate, explicitly audited operation and is intentionally not exposed
# by this API.

from hashlib import sha256 as _sha256
from datetime import datetime as _review_datetime, timezone as _review_timezone
from urllib.parse import urlparse as _urlparse
from pydantic import BaseModel as _ReviewBaseModel, Field as _ReviewField
from api.support import auth_identity_var as _review_identity_var


class EvidenceReviewSubmission(_ReviewBaseModel):
    claim_scope: str = _ReviewField(default="empirical_claim_support", min_length=3, max_length=500)


class EvidenceReviewDecision(_ReviewBaseModel):
    decision: str = _ReviewField(..., pattern="^(approved|rejected)$")
    notes: str = _ReviewField(..., min_length=3, max_length=4000)
    source_id: Optional[str] = _ReviewField(default=None, max_length=500)
    source_url: Optional[str] = _ReviewField(default=None, max_length=2048)
    source_title: Optional[str] = _ReviewField(default=None, max_length=500)
    source_type: Optional[str] = _ReviewField(default=None, max_length=100)
    trust_class: Optional[str] = _ReviewField(default=None, max_length=100)


def _evidence_review_client():
    """Return only the configured service-role client; never fall back to anon."""
    from services.supabase import supabase_service
    return getattr(supabase_service, "service_client", None)


def _evidence_review_actor(request: Request, *, require_human: bool = True) -> tuple[str, object]:
    """Resolve the audit actor from canonical auth or a verified KCPG human envelope.

    KCPG authenticates upstream to this API with a species service credential.
    For governed human decisions, the gateway additionally signs the actor,
    role, workspace and request ID. Never trust unsigned X-User-* headers.
    """
    identity = _review_identity_var.get()
    if identity is None:
        raise HTTPException(status_code=401, detail="Authenticated identity required")
    if not identity.has_permission("write"):
        raise HTTPException(status_code=403, detail="Workspace write permission required")

    # A signed gateway context is installed by the canonical request-context
    # dependency only after HMAC, freshness and workspace-binding checks pass.
    context = getattr(request.state, "request_context", None)
    metadata = getattr(context, "metadata", None) or {}
    actor_id = getattr(context, "principal_id", None)
    actor_role = str(getattr(context, "role", "") or "").lower()
    workspace_id = _resolve_workspace_from_request(request)
    verified_gateway_human = (
        getattr(context, "principal_type", "") == "human"
        and metadata.get("authority_source") == "verified_kcpg_hmac"
        and bool(actor_id)
        and bool(workspace_id)
        and str(getattr(context, "workspace_id", "")) == str(workspace_id)
        and actor_role in {"operator", "admin", "owner"}
    )
    if verified_gateway_human:
        return str(actor_id), context

    user_id = getattr(identity, "user_id", None)
    if require_human and (getattr(identity, "is_species", False) or not user_id):
        raise HTTPException(status_code=403, detail="A verified human reviewer identity is required")
    actor = str(user_id or f"service:{getattr(identity, 'method', 'unknown')}")
    return actor, identity


def _valid_evidence_url(value: Optional[str]) -> bool:
    if not value:
        return False
    parsed = _urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not any(ch.isspace() for ch in value)


def _evidence_review_payload(row: dict) -> dict:
    allowed = (
        "id", "knowledge_entry_id", "workspace_id", "brand_id", "source_id",
        "source_url", "source_title", "source_type", "trust_class",
        "claim_scope", "content_sha256", "review_status", "review_notes",
        "reviewed_by", "reviewed_at", "created_at", "updated_at",
    )
    return {key: row.get(key) for key in allowed}


@router.post("/brands/{brand_id}/knowledge/{knowledge_id}/evidence-review")
async def submit_knowledge_evidence_review(
    brand_id: str,
    knowledge_id: str,
    body: EvidenceReviewSubmission,
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Submit one exact knowledge-entry version for reusable source review."""
    try:
        await require_workspace_ownership("brand", brand_id, request, request.app.state.container)
        actor, _identity = _evidence_review_actor(request, require_human=False)
        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(request.app.state.container.resolve, KnowledgeRepository)
        entry = await _async_wrap(repo.find_by_id, knowledge_id)
        if not entry or entry.brand_id != brand_id:
            raise HTTPException(status_code=404, detail="Knowledge entry not found")

        content_hash = _sha256(entry.content.encode("utf-8")).hexdigest()
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        source_id = str(metadata.get("source_id") or metadata.get("sourceId") or f"unverified-entry:{entry.id}").strip()
        source_url = metadata.get("source_url") or metadata.get("sourceUrl")
        if source_url and not _valid_evidence_url(str(source_url)):
            source_url = None

        client = _evidence_review_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Evidence review storage unavailable")

        existing = await _async_wrap(
            lambda: client.table("knowledge_evidence_review_cases")
            .select("*").eq("knowledge_entry_id", entry.id)
            .eq("content_sha256", content_hash).limit(1).execute()
        )
        if existing.data:
            case = existing.data[0]
            if case.get("review_status") in {"pending", "approved"}:
                return _ok({"review_case": _evidence_review_payload(case), "reused": True})
            reopened = await _async_wrap(
                lambda: client.table("knowledge_evidence_review_cases")
                .update({
                    "review_status": "pending", "reviewed_by": actor,
                    "reviewed_at": _review_datetime.now(_review_timezone.utc).isoformat(),
                    "review_notes": "Reopened for a fresh source review.",
                    "source_id": source_id,
                    "source_url": source_url,
                }).eq("id", case["id"]).select("*").single().execute()
            )
            if not reopened.data:
                raise HTTPException(status_code=503, detail="Review case could not be reopened")
            return _ok({"review_case": _evidence_review_payload(reopened.data), "reused": True})

        try:
            inserted = await _async_wrap(
                lambda: client.table("knowledge_evidence_review_cases").insert({
                    "knowledge_entry_id": entry.id,
                    "workspace_id": entry.workspace_id,
                    "brand_id": brand_id,
                    "source_id": source_id,
                    "submitted_by": actor,
                    "source_url": source_url,
                    "source_title": metadata.get("source_title") or metadata.get("title"),
                    "source_type": metadata.get("source_type") or entry.doc_type,
                    "trust_class": metadata.get("trust_class"),
                    "claim_scope": body.claim_scope,
                    "content_sha256": content_hash,
                    "metadata_snapshot": metadata,
                    "review_status": "pending",
                }).select("*").single().execute()
            )
        except Exception:
            # A concurrent submitter may have won the unique
            # (knowledge_entry_id, content_sha256) insert race.
            raced = await _async_wrap(
                lambda: client.table("knowledge_evidence_review_cases")
                .select("*").eq("knowledge_entry_id", entry.id)
                .eq("content_sha256", content_hash).limit(1).execute()
            )
            if raced.data and raced.data[0].get("review_status") in {"pending", "approved"}:
                return _ok({"review_case": _evidence_review_payload(raced.data[0]), "reused": True})
            raise
        if not inserted.data:
            raise HTTPException(status_code=503, detail="Review case creation was not confirmed")
        return _ok({"review_case": _evidence_review_payload(inserted.data), "reused": False}, 201)
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST evidence review for {knowledge_id}")


@router.get("/brands/{brand_id}/knowledge-evidence-reviews")
async def list_knowledge_evidence_reviews(
    brand_id: str,
    request: Request,
    status: str = "pending",
    limit: int = 50,
    _auth: bool = Depends(require_auth),
):
    """List evidence review cases within the authenticated brand workspace."""
    try:
        await require_workspace_ownership("brand", brand_id, request, request.app.state.container)
        _evidence_review_actor(request, require_human=False)
        if status not in {"pending", "approved", "rejected", "superseded", "all"}:
            raise HTTPException(status_code=400, detail="Invalid review status")
        client = _evidence_review_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Evidence review storage unavailable")
        query = (
            client.table("knowledge_evidence_review_cases").select("*")
            .eq("brand_id", brand_id)
            .eq("workspace_id", _resolve_workspace_from_request(request))
            .order("created_at", desc=True).limit(min(100, max(1, limit)))
        )
        if status != "all":
            query = query.eq("review_status", status)
        result = await _async_wrap(query.execute)
        return _ok({"review_cases": [_evidence_review_payload(row) for row in (result.data or [])]})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"GET knowledge evidence reviews for {brand_id}")


@router.post("/brands/{brand_id}/knowledge-evidence-reviews/{case_id}/decision")
async def decide_knowledge_evidence_review(
    brand_id: str,
    case_id: str,
    body: EvidenceReviewDecision,
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Approve/reject a source for one immutable content version; no auto-promotion."""
    try:
        await require_workspace_ownership("brand", brand_id, request, request.app.state.container)
        actor, _identity = _evidence_review_actor(request, require_human=True)
        client = _evidence_review_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Evidence review storage unavailable")
        workspace_id = _resolve_workspace_from_request(request)
        found = await _async_wrap(
            lambda: client.table("knowledge_evidence_review_cases").select("*")
            .eq("id", case_id).eq("brand_id", brand_id)
            .eq("workspace_id", workspace_id).limit(1).execute()
        )
        if not found.data:
            raise HTTPException(status_code=404, detail="Review case not found")
        case = found.data[0]
        if case.get("review_status") != "pending":
            raise HTTPException(status_code=409, detail="Only pending review cases can receive a decision")

        from repositories.knowledge_repository import KnowledgeRepository
        repo = await _async_wrap(request.app.state.container.resolve, KnowledgeRepository)
        entry = await _async_wrap(repo.find_by_id, case["knowledge_entry_id"])
        if (
            not entry or entry.brand_id != brand_id
            or entry.workspace_id != workspace_id
            or _sha256(entry.content.encode("utf-8")).hexdigest() != case["content_sha256"]
        ):
            await _async_wrap(
                lambda: client.table("knowledge_evidence_review_cases")
                .update({"review_status": "superseded", "reviewed_by": actor,
                         "reviewed_at": _review_datetime.now(_review_timezone.utc).isoformat(),
                         "review_notes": "Content changed after submission; submit the new version."})
                .eq("id", case_id).eq("review_status", "pending").execute()
            )
            raise HTTPException(status_code=409, detail="Knowledge content changed; this review case was superseded")

        if body.decision == "approved":
            if not body.source_id or body.source_id.strip().startswith("unverified-entry:"):
                raise HTTPException(status_code=422, detail="Approval requires a stable source ID")
            if not _valid_evidence_url(body.source_url):
                raise HTTPException(status_code=422, detail="Approval requires an HTTP(S) source URL")
        updates = {
            "review_status": body.decision,
            "reviewed_by": actor,
            "reviewed_at": _review_datetime.now(_review_timezone.utc).isoformat(),
            "review_notes": body.notes,
        }
        if body.source_id:
            updates["source_id"] = body.source_id.strip()
        if body.source_url:
            if not _valid_evidence_url(body.source_url):
                raise HTTPException(status_code=422, detail="Source URL must be HTTP(S)")
            updates["source_url"] = body.source_url.strip()
        if body.source_title is not None:
            updates["source_title"] = body.source_title.strip()
        if body.source_type is not None:
            updates["source_type"] = body.source_type.strip()
        if body.trust_class is not None:
            updates["trust_class"] = body.trust_class.strip()

        changed = await _async_wrap(
            lambda: client.table("knowledge_evidence_review_cases").update(updates)
            .eq("id", case_id).eq("review_status", "pending").select("*").execute()
        )
        if not changed.data:
            raise HTTPException(status_code=409, detail="Review decision lost a concurrent update")
        # Deliberately do not modify knowledge_entries.metadata.verified here.
        return _ok({"review_case": _evidence_review_payload(changed.data[0]),
                    "evidence_promoted": False})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST evidence review decision {case_id}")


@router.post("/brands/{brand_id}/knowledge-evidence-reviews/{case_id}/promote")
async def promote_approved_knowledge_evidence(
    brand_id: str,
    case_id: str,
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """Promote one approved source through a transactional, audited DB function."""
    try:
        await require_workspace_ownership("brand", brand_id, request, request.app.state.container)
        actor, _identity = _evidence_review_actor(require_human=True)
        client = _evidence_review_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Evidence promotion storage unavailable")
        workspace_id = _resolve_workspace_from_request(request)
        found = await _async_wrap(
            lambda: client.table("knowledge_evidence_review_cases").select("id")
            .eq("id", case_id).eq("brand_id", brand_id)
            .eq("workspace_id", workspace_id).eq("review_status", "approved")
            .limit(1).execute()
        )
        if not found.data:
            raise HTTPException(status_code=404, detail="Approved review case not found")
        promoted = await _async_wrap(
            lambda: client.rpc(
                "promote_approved_knowledge_evidence",
                {"p_case_id": case_id, "p_actor_id": actor},
            ).execute()
        )
        if promoted.data is None:
            raise HTTPException(status_code=503, detail="Evidence promotion was not confirmed")
        return _ok({"promotion": promoted.data})
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, f"POST evidence promotion {case_id}")
