from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks, FastAPI
from typing import Optional
import threading
from api.dependencies import *
from api.dependencies import _async_wrap
from api.v1_models import *
from api.v1_helpers import *
from api.v1_helpers import _ok, _err, _safe_err
from api.v1_stores import *
from platform_version import PLATFORM_VERSION

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# MONITORING SSE — Authenticated Log Streaming
# ═══════════════════════════════════════════════════════════════════════════

# In-memory store for short-lived stream tokens
_stream_tokens: dict[str, dict] = {}
_stream_tokens_lock = threading.Lock()

STREAM_TOKEN_TTL_SECONDS = 30  # Tokens expire after 30 seconds


@router.post("/monitoring/stream-token")
async def generate_stream_token(
    _auth: bool = Depends(require_auth),
):
    """
    Generate a short-lived token for SSE streaming.
    
    Browser EventSource cannot send custom headers (no Authorization header).
    Instead, the client first calls this endpoint with proper auth,
    gets a short-lived token, and passes it as a query parameter to the SSE endpoint.
    
    Token expires in 30 seconds — enough time to establish the EventSource connection.
    """
    token = 'KDO_STREAM_' + _secrets_mod.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=STREAM_TOKEN_TTL_SECONDS)

    with _stream_tokens_lock:
        _stream_tokens[token] = {
            "expires_at": expires_at,
            "workspace_id": auth_workspace_id_var.get(),
            "created_at": datetime.now(timezone.utc),
        }

        # Clean expired tokens
        now = datetime.now(timezone.utc)
        expired = [t for t, v in _stream_tokens.items() if v["expires_at"] < now]
        for t in expired:
            del _stream_tokens[t]

    return _ok({
        "token": token,
        "expires_in_seconds": STREAM_TOKEN_TTL_SECONDS,
        "stream_url": f"/api/v1/monitoring/stream?token={token}",
    })


@router.get("/monitoring/stream")
async def monitoring_stream(
    request: Request,
    token: str = "",
    level: str = "",
    module: str = "",
):
    """
    Authenticated SSE stream of log events.
    
    Connect with EventSource after obtaining a stream token:
      POST /api/v1/monitoring/stream-token  (with Authorization header)
      → GET /api/v1/monitoring/stream?token=xxx
    
    Sends two event types:
      - event: stats    → periodic statistics snapshot (every 15s)
      - event: log      → individual log entries as they occur
      - event: heartbeat → keepalive ping (every 30s)
    
    Query params:
      - token: Required. Short-lived stream token from /monitoring/stream-token
      - level: Optional. Filter by log level (ERROR, WARNING, INFO)
      - module: Optional. Filter by module name

    NOTE (INCIDENT-2026-10-16 follow-up): LogBuffer has no workspace_id on
    LogEntry, so this stream cannot isolate logs by workspace at the callback
    level. The immediate impact is bounded: LogBuffer currently has no
    production writers (see core/log_buffer.py — no callsites of .add()), so
    the buffer is empty and nothing leaks. Before any writer is wired up,
    LogEntry must gain a workspace_id field and this handler must filter on
    it. Tracked separately; not a live leak today.
    """
    # Validate stream token
    if not token:
        return _err("Stream token is required. Obtain one via POST /api/v1/monitoring/stream-token", 401)

    with _stream_tokens_lock:
        token_data = _stream_tokens.get(token)
        if not token_data:
            return _err("Invalid or expired stream token. Obtain a new one.", 401)

        now = datetime.now(timezone.utc)
        if token_data["expires_at"] < now:
            del _stream_tokens[token]
            return _err("Stream token has expired. Obtain a new one.", 401)

        # Token is valid — consume it (single use)
        del _stream_tokens[token]

    workspace_id = token_data.get("workspace_id")

    # ── SSE Generator ──────────────────────────────────────────────────
    async def event_generator():
        from core.log_buffer import LogBuffer
        import json

        buf = LogBuffer.get_instance()
        queue: asyncio.Queue = asyncio.Queue()

        # Filter config
        level_filter = level.upper() if level else None
        module_filter = module.lower() if module else None

        def on_log(entry):
            """Callback from LogBuffer — push to async queue.

            TODO(INCIDENT-2026-10-16-followup): LogEntry has no workspace_id.
            When LogBuffer writers are introduced, this callback MUST filter
            by workspace_id before enqueueing, or cross-workspace logs will
            stream to every connected client. Fail closed until then.
            """
            try:
                # Apply filters
                if level_filter and entry.level != level_filter:
                    return
                if module_filter and module_filter not in entry.module.lower():
                    return

                queue.put_nowait(("log", entry.to_dict()))
            except Exception:
                pass

        # Subscribe to log buffer
        buf.subscribe(on_log)

        # Send initial stats
        stats = buf.get_stats()
        stats["as_of"] = datetime.now(timezone.utc).isoformat()
        yield f"event: stats\ndata: {json.dumps(stats)}\n\n"

        # Periodic stats + heartbeat
        last_stats_time = time.monotonic()
        last_heartbeat = time.monotonic()

        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                now_mono = time.monotonic()

                # Send stats every 15 seconds
                if now_mono - last_stats_time >= 15:
                    stats = buf.get_stats()
                    stats["as_of"] = datetime.now(timezone.utc).isoformat()
                    yield f"event: stats\ndata: {json.dumps(stats)}\n\n"
                    last_stats_time = now_mono

                # Send heartbeat every 30 seconds
                if now_mono - last_heartbeat >= 30:
                    yield f"event: heartbeat\ndata: {json.dumps({'time': datetime.now(timezone.utc).isoformat()})}\n\n"
                    last_heartbeat = now_mono

                # Drain log events from the queue
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    pass

        finally:
            buf.unsubscribe(on_log)
            # Drain remaining items
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/monitoring/stats")
async def monitoring_stats(
    _auth: bool = Depends(require_auth),
):
    """
    Get current monitoring statistics snapshot.
    
    Returns the canonical monitoring contract:
      { total, errors, warnings, success, rate, as_of }

    NOTE: sourced from the in-process LogBuffer, which currently has no
    production writers. All counters will read zero until the buffer is
    wired up. See the /monitoring/stream docstring for context.
    """
    try:
        from core.log_buffer import LogBuffer
        buf = LogBuffer.get_instance()
        raw = buf.get_stats()

        total = raw.get("total_logs", 0)
        errors = raw.get("errors", 0)
        warnings = raw.get("warnings", 0)
        info = raw.get("info", 0)
        success = total - errors - warnings if total > 0 else 0
        rate = round((success / total) * 100, 1) if total > 0 else 100.0

        return _ok({
            "total": total,
            "errors": errors,
            "warnings": warnings,
            "success": max(success, 0),
            "rate": rate,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": raw.get("uptime_seconds", 0),
        })
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/monitoring/stats")


@router.get("/logs/stats")
async def logs_stats(_auth: bool = Depends(require_auth)):
    """Spec §6: Return actual log counters — total, errors, warnings, success, rate.

    NOTE: the `rate` field is not actually a rate — LogEntry.to_dict() does
    not emit a `minute` field, so the denominator is always 1 and rate == total.
    Kept for API compatibility; will be corrected or removed in a follow-up.
    """
    from core.log_buffer import LogBuffer
    buf = LogBuffer.get_instance()
    all_entries = buf.get_recent(limit=1000)
    total = len(all_entries)
    errors = sum(1 for e in all_entries if (e.get("level") or "").upper() in ("ERROR", "CRITICAL"))
    warnings = sum(1 for e in all_entries if (e.get("level") or "").upper() == "WARNING")
    success = sum(1 for e in all_entries if (e.get("level") or "").upper() in ("INFO", "DEBUG"))
    rate = round(total / max(len({e.get("minute", "x") for e in all_entries}), 1), 2)
    return _ok({
        "total": total,
        "errors": errors,
        "warnings": warnings,
        "success": success,
        "rate": rate,
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/logs/stream")
async def logs_stream(_auth: bool = Depends(require_auth)):
    """
    Spec §6: Real-time log streaming via Server-Sent Events.
    Polls the LogBuffer and pushes new entries to the client.
    Does not expose sensitive log content.

    NOTE: same as /monitoring/stream — buffer has no writers today, so the
    stream is empty. See the /monitoring/stream docstring.
    """
    from core.log_buffer import LogBuffer
    from fastapi.responses import StreamingResponse

    async def _event_generator():
        buf = LogBuffer.get_instance()
        last_count = 0
        try:
            while True:
                entries = buf.get_recent(limit=100)
                if len(entries) > last_count:
                    new_entries = entries[:len(entries) - last_count]
                    for entry in reversed(new_entries):
                        import json as _j
                        data = _j.dumps({
                            "timestamp": entry.get("timestamp"),
                            "level": entry.get("level"),
                            "message": str(entry.get("message", ""))[:500],
                        })
                        yield f"data: {data}\n\n"
                    last_count = len(entries)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/context/me")
async def get_context_me(request: Request, _auth: bool = Depends(require_auth)):
    """Return the canonical authenticated principal/workspace context."""
    from core.request_context import get_request_context
    ctx = get_request_context()
    identity = getattr(request.state, "auth_identity", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Authentication context unavailable")
    return _ok({
        "principal_id": ctx.principal_id,
        "workspace_id": ctx.workspace_id,
        "role": ctx.role,
        "permissions": list(ctx.permissions or ()),
        "species_privileges": bool(ctx.species_privileges),
        "auth_method": ctx.auth_method or getattr(identity, "method", ""),
        "correlation_id": ctx.correlation_id,
        "brand_id": ctx.brand_id,
        "email": ctx.user_email or getattr(identity, "user_email", None),
    })


@router.get("/status")
async def get_status():
    """Get platform status — public."""
    from config.settings import settings
    return _ok({
        "version": PLATFORM_VERSION,
        "environment": settings.environment,
        "config": {
            "ai_provider": settings.ai_provider,
            "gemini_model": settings.gemini_model,
            "supabase_enabled": settings.supabase_enabled,
        },
    })


@router.get("/features")
async def get_features(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """
    Get feature flags for the authenticated workspace.
    
    Loads from Supabase if available, falls back to defaults.
    Workspace-scoped — different workspaces can have different flags.
    """
    try:
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            # Previously fell back to the string "global", which was a shared
            # bucket any unauthenticated-workspace caller could read from and
            # write to. Fail closed instead.
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        # Start with defaults
        from config.settings import settings
        flags = {
            "auto_reply": True,
            "auto_post": True,
            "guardian_scan": settings.guardian_configured,
        }

        # Load persisted flags from Supabase (workspace-scoped)
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = await _async_wrap(
                    lambda: supabase_service.client.table("feature_flags")
                    .select("feature, enabled")
                    .eq("workspace_id", workspace_id)
                    .execute()
                )
                if result.data:
                    for row in result.data:
                        flags[row["feature"]] = row["enabled"]
        except Exception as e:
            logger.debug("Feature flags read from Supabase failed, using defaults: %s", e)

        return _ok({
            "features": flags,
            "workspace_id": workspace_id,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/features")

@router.post("/features/toggle")
async def toggle_feature(
    body: dict,
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §8: Toggle a feature flag — persists to Supabase if available,
    falls back to in-memory. State survives restarts when Supabase is configured.

    Tenancy: resolve_plugin_workspace ignores body.workspace_id for ordinary
    principals. Only species operators can cross-select, per RequestContext
    .effective_workspace.
    """
    try:
        feature = body.get("feature")
        enabled = body.get("enabled")
        workspace_id = resolve_plugin_workspace(body.get("workspace_id"))

        if not feature:
            return _err("feature name is required", 400)
        if enabled is None:
            return _err("enabled (boolean) is required", 400)

        record = {
            "feature": feature,
            "enabled": bool(enabled),
            "workspace_id": workspace_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": auth_user_email_var.get() or "system",
        }

        # Persist to Supabase (durable — survives restarts and deployments)
        persisted = False
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                await _async_wrap(
                    lambda: supabase_service.client.table("feature_flags")
                    .upsert({**record, "id": f"{workspace_id}:{feature}"})
                    .execute()
                )
                persisted = True
        except Exception as pe:
            logger.warning("Feature flag persistence to Supabase failed: %s", pe)

        return _ok({
            "feature": feature,
            "enabled": bool(enabled),
            "workspace_id": workspace_id,
            "persisted": persisted,
        })
    except Exception as exc:
        return _safe_err(exc, "POST /api/v1/features/toggle")


@router.get("/rate-limits")
async def get_rate_limits(
    request: Request,
    _auth: bool = Depends(require_auth),
):
    """
    Get rate limit configuration for the authenticated workspace.
    Loads from Supabase if available, falls back to defaults.
    """
    try:
        workspace_id = _resolve_workspace_from_request(request)
        if not workspace_id:
            # Previously fell back to the string "global". Fail closed.
            raise HTTPException(status_code=403, detail="Authenticated workspace is required")

        # Default config
        config = {
            "limit": 30,
            "window": 60,
            "scope": "user",
            "workspace_id": workspace_id,
            "provider": "default",
            "enabled": True,
        }

        # Load from Supabase
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                result = await _async_wrap(
                    lambda: supabase_service.client.table("rate_limit_config")
                    .select("*")
                    .eq("workspace_id", workspace_id)
                    .limit(1)
                    .execute()
                )
                if result.data:
                    row = result.data[0]
                    config = {
                        "limit": row.get("limit", 30),
                        "window": row.get("window", 60),
                        "scope": row.get("scope", "user"),
                        "workspace_id": row.get("workspace_id", workspace_id),
                        "provider": row.get("provider", "default"),
                        "enabled": row.get("enabled", True),
                    }
        except Exception as e:
            logger.debug("Rate limits read from Supabase failed, using defaults: %s", e)

        return _ok({
            "rate_limits": config,
            "workspace_id": workspace_id,
        })
    except HTTPException:
        raise
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/rate-limits")

@router.put("/rate-limits/config")
async def update_rate_limit_config(
    body: RateLimitConfigRequest,
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """
    Spec §9: Update rate-limit configuration.
    Validates and persists the rate-limit rule to Supabase when available.

    Tenancy: same as /features/toggle — resolve_plugin_workspace ignores
    body.workspace_id for ordinary principals.
    """
    try:
        workspace_id = resolve_plugin_workspace(body.workspace_id)
        record = {
            "limit": body.limit,
            "window": body.window,
            "scope": body.scope,
            "workspace_id": workspace_id,
            "provider": body.provider,
            "enabled": body.enabled,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        persisted = False
        try:
            from services.supabase import supabase_service
            if supabase_service and supabase_service.client:
                rule_id = f"{body.scope}:{workspace_id}:{body.provider or 'default'}"
                await _async_wrap(
                    lambda: supabase_service.client.table("rate_limit_config")
                    .upsert({**record, "id": rule_id})
                    .execute()
                )
                persisted = True
        except Exception as pe:
            logger.warning("Rate-limit config persistence failed: %s", pe)

        return _ok({"updated": True, "config": record, "persisted": persisted})
    except Exception as exc:
        return _safe_err(exc, "PUT /api/v1/rate-limits/config")


@router.post("/rate-limits/unblock")
async def unblock_user(
    body: UnblockRequest,
    _auth: bool = Depends(require_auth),
):
    """Unblock a rate-limited user."""
    from utils.rate_limiter import rate_limiter
    unblocked = await _async_wrap(rate_limiter.unblock, body.identifier)
    return _ok({"unblocked": unblocked})


@router.get("/social-accounts")
async def list_social_accounts(
    _auth: bool = Depends(require_auth),
):
    """List the caller's connected social accounts. Never returns credentials.

    Reads from social_accounts (the canonical domain table) via
    SocialAccountRepository.find_by_workspace(), scoped to the caller's
    workspace. Response uses SocialAccount.to_public_dict(), which
    allowlists non-credential fields.
    """
    workspace_id = auth_workspace_id_var.get()
    if not workspace_id:
        raise HTTPException(status_code=403, detail="Authenticated workspace is required")

    from repositories.social_account_repository import SocialAccountRepository
    repo = SocialAccountRepository()
    try:
        accounts = await _async_wrap(repo.find_by_workspace, workspace_id)
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/social-accounts")

    return _ok({
        "accounts": [a.to_public_dict() for a in (accounts or [])],
        "workspace_id": workspace_id,
    })


@router.get("/tenants")
async def get_tenants(
    _auth: bool = Depends(require_auth),
    _admin=Depends(require_workspace_admin),
):
    """DEPRECATED. Use GET /api/v1/social-accounts instead.

    INCIDENT-2026-10-16: this endpoint previously returned every active row
    from the tenants table to any authenticated user. The tenants table
    stores page_access_token and verify_token, so this was credential
    disclosure, not just data disclosure.

    Immediate mitigation:
      - require_workspace_admin gate added.
      - Explicit column whitelist — never serialize credential columns,
        even for admins.

    Scheduled for removal: 2026-11-16.
    """
    from services.supabase import supabase_service
    if supabase_service and supabase_service.client:
        result = await _async_wrap(
            lambda: supabase_service.client.table("tenants")
            .select("id, name, page_id, plan, status, workspace_id, created_at, updated_at")
            .eq("status", "active")
            .execute()
        )
        return _ok({"tenants": result.data or []})
    return _ok({"tenants": []})


# POST /tenants/switch — REMOVED 2026-10-16.
# No callers found in this repo or the frontend. The handler returned a
# hardcoded {"switched": True} without switching anything — a stub that
# lies. If tenant switching becomes a real requirement, build it with
# defined storage semantics (session-scoped contextvar, or a persisted
# user preference) and a proper contract.


@router.get("/notifications")
async def get_notifications(
    unread_only: bool = False,
    limit: int = 100,
    _auth: bool = Depends(require_auth),
):
    """Read the durable workspace-scoped operator notification projection."""
    from fastapi import HTTPException
    from services.notification_inbox_repository import NotificationInboxRepository

    workspace_id = auth_workspace_id_var.get()
    if not workspace_id:
        raise HTTPException(status_code=403, detail="Authenticated workspace is required")
    limit = max(1, min(limit, 200))
    try:
        notifications = await _async_wrap(
            NotificationInboxRepository().list_for_workspace,
            workspace_id, unread_only=unread_only, limit=limit,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Notification persistence is unavailable") from exc
    return _ok({"notifications": notifications, "workspace_id": workspace_id, "truth": "OBSERVED"})


@router.post("/notifications/{notif_id}/read")
async def mark_notification_read(
    notif_id: str,
    _auth: bool = Depends(require_auth),
):
    """Record operator read-state against the authoritative notification projection."""
    from fastapi import HTTPException
    from services.notification_inbox_repository import NotificationInboxRepository

    workspace_id = auth_workspace_id_var.get()
    if not workspace_id:
        raise HTTPException(status_code=403, detail="Authenticated workspace is required")
    try:
        notification = await _async_wrap(
            NotificationInboxRepository().mark_read, workspace_id, notif_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Notification persistence is unavailable") from exc
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found in workspace")
    return _ok({"notification": notification, "workspace_id": workspace_id})


@router.get("/integrations")
async def get_integrations(_auth: bool = Depends(require_auth)):
    """Get integration statuses — multi-tenant aware."""
    from config.settings import settings
    
    # Facebook — check global + per-brand
    facebook_connected = bool(settings.page_access_token)
    if not facebook_connected:
        try:
            from repositories.social_account_repository import SocialAccountRepository
            repo = SocialAccountRepository()
            accounts = repo.find_by_platform("facebook")
            facebook_connected = any(
                getattr(a, 'enabled', False) for a in (accounts or [])
            )
        except Exception:
            pass
    
    integrations = [
        {"id": "facebook", "connected": facebook_connected},
        {"id": "gemini", "connected": bool(settings.gemini_key)},
        {"id": "supabase", "connected": settings.supabase_enabled},
        {"id": "twitter", "connected": bool(settings.twitter_access_token)},
        {"id": "linkedin", "connected": bool(settings.linkedin_access_token)},
        {"id": "whatsapp", "connected": bool(settings.whatsapp_token)},
        {"id": "redis", "connected": bool(settings.upstash_redis_rest_url)},
    ]
    return _ok({
        "integrations": integrations,
        "connected": sum(1 for i in integrations if i["connected"]),
    })

@router.get("/audit-logs")
async def get_audit_logs(
    audit_log: AsyncAuditLog = Depends(get_audit_log),
    _auth: bool = Depends(require_auth),
    limit: int = 50,
):
    """Get audit logs — workspace-scoped.

    NOTE: filter runs post-fetch against the in-process audit log, and
    entries with workspace_id=None are shown to every caller. That is a
    deliberate choice pending review: entries without a workspace are
    presumed platform-scoped. If that assumption is wrong, this leaks.
    Flagged for review; not fixed here to avoid masking legitimate
    platform-level entries.
    """
    limit = min(200, max(1, limit))
    workspace_id = auth_workspace_id_var.get()
    logs = await audit_log.get_recent(limit)
    
    # Filter by workspace if not admin
    if workspace_id:
        logs = [
            l for l in logs
            if l.get("workspace_id") == workspace_id or l.get("workspace_id") is None
        ]
    
    return _ok({
        "logs": logs,
        "count": len(logs),
        "workspace_id": workspace_id,
    })

@router.post("/scan")
async def trigger_scan_shortcut(
    background_tasks: BackgroundTasks,
    container=Depends(get_container),
    _auth: bool = Depends(require_auth),
):
    """Shortcut to trigger Guardian scan."""
    return await trigger_scan(background_tasks, container, _auth)


@router.get("/scan")
async def get_scan_status(container=Depends(get_container)):
    """Get scan status (aliases guardian/status)."""
    return await guardian_status(container)


# ═══════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═══════════════════════════════════════════════════════════════════════════

def create_app(container=None) -> FastAPI:
    """Deprecated compatibility wrapper; the canonical app factory is main.create_app."""
    from main import create_app as canonical_create_app
    return canonical_create_app()
