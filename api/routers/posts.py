from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *
from api.support import MAX_PAGE_SIZE

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# POSTS
#
# INCIDENT-2026-10-16-posts:
#   GET /posts treated workspace_id=None as "master token / admin mode"
#   and returned platform-wide posts. workspace_id=None does not
#   distinguish a master token from an expired session or a contextvar
#   race, so the fail-open path was a cross-tenant read leak.
#
#   Now: workspace_id=None is refused (403) unless the caller has
#   species_privileges. Only that explicitly-admin path returns
#   cross-workspace data.
#
#   POST /posts was already correctly scoped via resolve_plugin_workspace
#   (which ignores client-supplied workspace_id for ordinary principals)
#   and writes workspace_id into scheduled_posts and draft records.
#
# INCIDENT-2026-10-26-posts-nameerror:
#   GET /posts 500'd with
#     NameError: name 'get_request_context' is not defined
#     File "/app/api/routers/posts.py", line 65, in get_posts
#   The function `get_request_context()` does not exist anywhere in the
#   codebase — it was a leftover call site from a removed helper. The
#   fix reads the request context from `request.state.request_context`
#   directly, which is the same accessor `require_auth` uses. No import
#   is needed: `request` is already a handler parameter.
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/posts")
async def get_posts(
    request: Request,
    draft_store: DraftStore = Depends(get_draft_store),
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
    page: int = 1,
    per_page: int = 20,
    state: str = "",
):
    """List posts scoped to the authenticated workspace.

    Architecture §31 — Tenant Isolation:
      Every query is scoped by workspace. Users from Workspace A
      must never see posts belonging to Workspace B.

    INCIDENT-2026-10-16-posts:
      Previously, a `workspace_id` of None caused the handler to return
      platform-wide posts (all workspaces). The comment labeled this
      "master token / admin mode", but workspace_id=None does not
      distinguish a master token from an expired session, a contextvar
      race, or a species user with no workspace binding. Fail-open on
      unknown identity is a cross-tenant read leak.

      Now: workspace_id=None is refused (403) UNLESS the caller has
      species_privileges (explicit platform-admin signal). Only that
      path returns cross-workspace data, and it is opt-in via role.
    """
    try:
        page = max(1, page)
        per_page = min(MAX_PAGE_SIZE, max(1, per_page))
        state_filter = state.lower()

        # Resolve the authenticated workspace and admin status.
        workspace_id = _resolve_workspace_from_request(request)
        ctx = getattr(request.state, "request_context", None)
        is_platform_admin = bool(ctx and getattr(ctx, "species_privileges", False))

        if not workspace_id and not is_platform_admin:
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        # Cross-workspace visibility is granted only to species admins
        # with no bound workspace. Species admins with a bound workspace
        # are scoped to it like any other caller.
        unscoped_admin = is_platform_admin and not workspace_id

        # ── Drafts (workspace-scoped) ────────────────────────────────
        drafts = await draft_store.get_all()
        if not unscoped_admin:
            drafts = [d for d in drafts if d.get("workspace_id") == workspace_id]

        # ── Published posts (workspace-scoped) ────────────────────────
        published_posts = []
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                query = supabase_service.client.table("post_history").select("*")

                # Workspace isolation — never return cross-workspace posts
                # unless the caller is a species admin with no bound workspace.
                if not unscoped_admin:
                    query = query.eq("workspace_id", workspace_id)

                result = await _async_wrap(lambda: query.execute())
                published_posts = [
                    {**p, "state": "published"} for p in (result.data or [])
                ]
        except Exception:
            pass

        # ── Also include automation_jobs for published posts ──────────
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                job_query = supabase_service.client.table("automation_jobs") \
                    .select("*") \
                    .eq("status", "PUBLISHED") \
                    .order("completed_at", ascending=False) \
                    .limit(per_page)

                if not unscoped_admin:
                    job_query = job_query.eq("workspace_id", workspace_id)

                job_result = await _async_wrap(lambda: job_query.execute())

                for job in (job_result.data or []):
                    # Avoid duplicates if already in post_history
                    existing_ids = {p.get("id") for p in published_posts}
                    if job.get("job_id") not in existing_ids:
                        published_posts.append({
                            "id": job.get("job_id"),
                            "topic": job.get("topic"),
                            "caption": job.get("topic"),  # Job doesn't store full caption
                            "platform": job.get("platform", "facebook"),
                            "state": "published",
                            "workspace_id": job.get("workspace_id"),
                            "brand_id": job.get("brand_id"),
                            "created_at": job.get("completed_at"),
                            "job_id": job.get("job_id"),
                        })
        except Exception:
            pass

        # ── Filter by state ──────────────────────────────────────────
        if state_filter == "draft":
            all_posts = drafts
        elif state_filter == "published":
            all_posts = published_posts
        else:
            all_posts = drafts + published_posts

        # Sort by date descending
        all_posts.sort(
            key=lambda p: p.get("created_at") or "",
            reverse=True,
        )

        start = (page - 1) * per_page
        posts = all_posts[start : start + per_page]

        return _ok({
            "posts": posts,
            "page": page,
            "per_page": per_page,
            "total": len(all_posts),
            "drafts": len(drafts),
            "workspace_id": workspace_id,
            "scope": "platform" if unscoped_admin else "workspace",
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/posts")


@router.post("/posts")
async def create_post(
    body: PostCreateRequest,
    draft_store: DraftStore = Depends(get_draft_store),
    container=Depends(get_container),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Create a post draft or trigger immediate publishing."""
    try:
        workspace_id = resolve_plugin_workspace(body.workspace_id)
        state = (body.state or "draft").lower()

        if state in ("publish", "published"):
            if not body.brand_id:
                return _err("brand_id is required for publishing a post", 400)

            # ✅ V1.6 — Auto-generate caption via ContentEngine when:
            #   (a) caller sets ai_generate=True, OR
            #   (b) caption is empty and a topic was supplied.
            # Manual captions are preserved verbatim (backward compatible).
            # Must run BEFORE decision_id issuance and scheduled_for handling
            # so the AI caption flows through both publish-now and schedule paths.
            _topic = (body.topic or "").strip()
            _caption_in = (body.caption or "").strip()
            _should_generate = bool(body.ai_generate) or (not _caption_in and bool(_topic))

            if _should_generate:
                if not _topic:
                    return _err(
                        "topic is required when caption is empty or ai_generate=true",
                        400,
                    )
                try:
                    from services.content_engine import content_engine
                    import asyncio as _asyncio

                    # Brand object (best-effort; engine falls back to defaults)
                    _brand_obj = None
                    try:
                        from repositories.brand_repository import BrandRepository
                        _brand_repo = container.try_resolve(BrandRepository)
                        if _brand_repo is None:
                            _brand_repo = BrandRepository()
                        _brand_obj = _brand_repo.find_by_id(body.brand_id)
                    except Exception as _be:
                        logger.debug("Brand lookup for content generation failed: %s", _be)

                    # AI profile (best-effort; engine falls back)
                    _ai_profile_obj = None
                    try:
                        from repositories.ai_profile_repository import AIProfileRepository
                        _ap_repo = container.try_resolve(AIProfileRepository)
                        if _ap_repo is None:
                            _ap_repo = AIProfileRepository()
                        for _method in ("find_by_brand", "find_default_for_brand", "find_default"):
                            if hasattr(_ap_repo, _method):
                                try:
                                    _ai_profile_obj = getattr(_ap_repo, _method)(body.brand_id)
                                    if _ai_profile_obj is not None:
                                        break
                                except Exception:
                                    continue
                    except Exception as _pe:
                        logger.debug("AIProfile lookup failed: %s", _pe)

                    _gen_result = await _asyncio.wait_for(
                        content_engine.generate_post(
                            brand=_brand_obj,
                            ai_profile=_ai_profile_obj,
                            topic=_topic,
                        ),
                        timeout=90.0,
                    )

                    _generated_caption = ""
                    if isinstance(_gen_result, dict):
                        _generated_caption = (
                            _gen_result.get("caption")
                            or _gen_result.get("text")
                            or ""
                        ).strip()

                    if not _generated_caption:
                        return _err("content generation returned an empty caption", 502)

                    # Overwrite body.caption so all downstream usages
                    # (audit, scheduled_posts, run_publish, response)
                    # pick up the AI-generated text automatically.
                    body.caption = _generated_caption
                    _intel_mode = (
                        _gen_result.get("intelligence_mode", "unknown")
                        if isinstance(_gen_result, dict)
                        else "unknown"
                    )
                    logger.info(
                        "Content engine generated caption (%d chars) topic='%s' "
                        "intelligence_mode=%s workspace=%s",
                        len(_generated_caption),
                        _topic[:80],
                        _intel_mode,
                        workspace_id,
                    )
                except _asyncio.TimeoutError:
                    return _err("content generation timed out", 504)
                except Exception as _ge:
                    logger.exception("Content generation failed")
                    return _err(f"content generation failed: {_ge}", 502)

            # A low-risk authenticated Post action is itself a trusted user
            # intent. Convert it into a normal DecisionGate record when the
            # client has not supplied one; never auto-authorize elevated risk.
            decision_id = body.decision_id
            if not decision_id:
                if body.risk_level not in ("trivial", "low"):
                    return _err("decision_id is required for medium/high-risk publishing", 403)
                try:
                    from core.publish_authorization import issue_internal_publish_decision
                    decision = issue_internal_publish_decision(
                        workspace_id=workspace_id, source="manual",
                        risk_level=body.risk_level, brand_id=body.brand_id,
                        platform=(body.platforms or ["facebook"])[0],
                    )
                    decision_id = decision.decision_id
                except Exception as exc:
                    return _err(f"publish authorization failed: {exc}", 403)
            else:
                # ✅ V1.6 — Validate a caller-supplied decision_id.
                if isinstance(decision_id, str) and decision_id.startswith("species_override:"):
                    logger.warning(
                        "species_override publish decision_id=%s workspace=%s risk=%s",
                        decision_id, workspace_id, body.risk_level,
                    )
                else:
                    try:
                        from core.decision_gate import decision_gate
                        _dec = decision_gate.get(decision_id)
                    except Exception as exc:
                        return _err(f"decision lookup failed: {exc}", 500)

                    if _dec is None:
                        return _err(f"unknown decision_id: {decision_id}", 400)
                    if str(getattr(_dec, "workspace_id", "")) != str(workspace_id):
                        return _err("decision_id does not belong to this workspace", 403)
                    if str(getattr(_dec, "risk_level", "")).lower() != str(body.risk_level).lower():
                        return _err(
                            f"decision_id risk_level ({_dec.risk_level}) "
                            f"does not match request ({body.risk_level})",
                            400,
                        )
                    if not getattr(_dec, "approved", False):
                        return _err("decision_id was not approved", 403)

            # POST /posts with scheduled_for is a durable scheduler intent, not
            # an immediate publish. The scheduler later creates the normal
            # automation job and drives the same canonical publish/outbox path.
            if body.scheduled_for:
                try:
                    from datetime import datetime as _dt
                    scheduled_for = _dt.fromisoformat(body.scheduled_for.replace("Z", "+00:00"))
                    if scheduled_for.tzinfo is None:
                        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
                    if scheduled_for <= datetime.now(timezone.utc):
                        return _err("scheduled_for must be in the future", 400)
                    from services.supabase import supabase_service
                    if not supabase_service or not supabase_service.client:
                        return _err("scheduler database is unavailable", 503)
                    scheduled_id = f"sp_{_uuid_mod.uuid4().hex}"
                    row = {
                        "id": scheduled_id,
                        "workspace_id": workspace_id,
                        "brand_id": body.brand_id,
                        "caption": body.caption,
                        "scheduled_for": scheduled_for.isoformat(),
                        "status": "queued",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await _async_wrap(lambda: supabase_service.client.table("scheduled_posts").insert(row).execute())
                    await append_audit(
                        audit_log, "post_publish_scheduled", f"scheduled:{scheduled_id}",
                        status_str="queued", detail=f"Brand: {body.brand_id}",
                    )
                    return _ok({
                        "published": False, "queued": True, "scheduled": True,
                        "scheduled_post_id": scheduled_id,
                        "scheduled_for": scheduled_for.isoformat(),
                        "brand_id": body.brand_id, "workspace_id": workspace_id,
                        "decision_id": decision_id,
                        "ai_generated": _should_generate,
                    }, 202)
                except ValueError as exc:
                    return _err(f"invalid scheduled_for: {exc}", 400)
                except Exception as exc:
                    return _safe_err(exc, "POST /api/v1/posts schedule")

            from services.publish_orchestration import run_publish
            platforms = body.platforms or ["facebook"]
            results = []
            for platform in platforms:
                result = await run_publish(
                    container=container,
                    workspace_id=workspace_id,
                    brand_id=body.brand_id,
                    platform=platform,
                    topic=body.topic,
                    caption=body.caption,
                    source="api_posts",
                    risk_level=body.risk_level,
                    decision_id=decision_id,
                    durable=True,
                    idempotency_key=body.idempotency_key,
                )
                results.append(result)

            ok_results = [r for r in results if isinstance(r, dict) and r.get("ok")]
            queued = [r for r in ok_results if r.get("queued")]
            if not ok_results or len(ok_results) != len(results):
                first_error = next((r for r in results if not r.get("ok")), results[0] if results else {})
                return _ok({"published": False, "queued": False, "results": results, "error": first_error.get("error")}, 403)

            outbox_ids = [r.get("outbox_id") for r in queued if r.get("outbox_id")]
            correlation_ids = [r.get("correlation_id") for r in queued if r.get("correlation_id")]
            await append_audit(
                audit_log, "post_publish_queued", f"outbox:{outbox_ids[0] if outbox_ids else 'unknown'}",
                status_str="queued", detail=f"Brand: {body.brand_id}",
            )
            return _ok({
                "published": False,
                "queued": bool(queued),
                "outbox_ids": outbox_ids,
                "correlation_ids": correlation_ids,
                "brand_id": body.brand_id,
                "workspace_id": workspace_id,
                "results": results,
                "ai_generated": _should_generate,
            }, 202)
        else:
            draft_id = f"draft-{_uuid_mod.uuid4().hex[:12]}"
            draft_data = {
                "id": draft_id,
                "draft_id": draft_id,
                "caption": body.caption,
                "topic": body.topic,
                "category": body.category,
                "state": "draft",
                "workspace_id": workspace_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await draft_store.set(draft_id, draft_data)
            await append_audit(
                audit_log, "post_draft_created", f"draft:{draft_id}",
                status_str="success",
            )
            return _ok({
                "draft": True,
                "draft_id": draft_id,
                "post": draft_data,
                "workspace_id": workspace_id,
            }, 201)
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/posts")



@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: str,
    request: Request,
    draft_store: DraftStore = Depends(get_draft_store),
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
):
    """Delete a draft owned by the authenticated workspace.

    Published posts and queued executions are deliberately not deleted by this
    endpoint. Their lifecycle requires their own governed cancellation or
    takedown contract; this route only removes a workspace-owned draft.
    """
    workspace_id = _resolve_workspace_from_request(request)
    if not workspace_id:
        raise HTTPException(status_code=403, detail="Authenticated workspace is required")

    drafts = await draft_store.get_all()
    owned_draft = next(
        (
            draft for draft in drafts
            if str(draft.get("id") or draft.get("draft_id") or "") == str(post_id)
            and str(draft.get("workspace_id") or "") == str(workspace_id)
        ),
        None,
    )
    if owned_draft is None:
        # Same response for unknown and other-workspace IDs prevents enumeration.
        raise HTTPException(status_code=404, detail="Draft not found")

    if str(owned_draft.get("state") or "draft").lower() != "draft":
        raise HTTPException(status_code=409, detail="Only draft posts can be deleted")

    deleted = await draft_store.delete(post_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Draft not found")

    await append_audit(
        audit_log,
        "post_draft_deleted",
        f"draft:{post_id}",
        status_str="success",
    )
    return _ok({
        "deleted": True,
        "post_id": post_id,
        "state": "draft",
        "workspace_id": workspace_id,
    })
