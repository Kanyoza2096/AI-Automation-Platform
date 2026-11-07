from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
from api.dependencies import *
from api.v1_models import *
from api.v1_helpers import *
from api.v1_stores import *

router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════
# SYSTEM, STATUS, FEATURES, RATE LIMITS, TENANTS, NOTIFICATIONS, INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/system/health")
async def get_system_health(request: Request):
    """Get system health status.

    Shape includes `services` map with `{ok, healthy, status}` so the control-plane
    Sidebar ORGANISM LINK can render servicesHealthy/servicesTotal honestly.
    """
    try:
        from core.reliability import HealthChecker
        container = request.app.state.container
        checker = await _async_wrap(container.resolve, HealthChecker)
        statuses = await _async_wrap(checker.check_all)
        if not isinstance(statuses, dict):
            statuses = {}
        services = {}
        for name, entry in statuses.items():
            if isinstance(entry, dict):
                ok = bool(entry.get("ok", entry.get("healthy", False)))
                services[name] = {
                    "ok": ok,
                    "healthy": ok,
                    "status": "healthy" if ok else str(entry.get("status", "degraded")),
                    **{k: v for k, v in entry.items() if k not in ("ok", "healthy", "status")},
                }
            else:
                services[str(name)] = {"ok": bool(entry), "healthy": bool(entry), "status": "healthy" if entry else "unknown"}
        # Fold organism vitals when available
        try:
            from core import organism as org_mod
            from core.request_context import require_request_context
            wid = str(require_request_context().workspace_id or "")
            org = None
            for fn_name in ("get_organism_for_workspace", "get_organism", "resolve_organism"):
                fn = getattr(org_mod, fn_name, None)
                if callable(fn) and wid:
                    try:
                        org = fn(wid)
                        break
                    except TypeError:
                        try:
                            org = fn()
                            break
                        except Exception:
                            pass
            if org is None and hasattr(org_mod, "_organisms") and wid:
                org = getattr(org_mod, "_organisms", {}).get(wid)
            if org is not None:
                life = getattr(org, "life_status", None) or getattr(org, "status", None)
                if hasattr(life, "value"):
                    life = life.value
                life_s = str(life or "").upper()
                services["organism"] = {
                    "ok": life_s in ("ALIVE", "HEALTHY", "RUNNING"),
                    "healthy": life_s in ("ALIVE", "HEALTHY", "RUNNING"),
                    "status": "healthy" if life_s in ("ALIVE", "HEALTHY", "RUNNING") else str(life or "unknown").lower(),
                }
        except Exception:
            pass
        all_ok = all(bool(s.get("ok")) for s in services.values()) if services else False
        any_ok = any(bool(s.get("ok")) for s in services.values()) if services else False
        status = "healthy" if all_ok and services else ("degraded" if any_ok else "unknown")
        from core.evidence_provenance import attach_evidence_provenance
        payload = {
            "status": status,
            "connectors": statuses,
            "services": services,
            "uptime_seconds": getattr(getattr(request.app.state, "started_at", None), "timestamp", lambda: None)()
            if False else None,
        }
        payload = attach_evidence_provenance(
            payload, source="kdo.system.health",
            authority="kdo.backend.health_checker", data_class="DERIVED",
        )
        return _ok(payload)
    except Exception as exc:
        return _safe_err(exc, "GET /api/v1/system/health")


@router.get("/system/connectors")
async def get_system_connectors(_auth: bool = Depends(require_auth)):
    """Get supported connectors."""
    from services.connectors.connector_factory import supported_platforms
    return _ok({"supported_connectors": supported_platforms()})


@router.get("/health/deep")
async def health_deep():
    """Deep health check — public. Multi-tenant aware."""
    from config.settings import settings

    # Facebook — check global + per-brand social accounts
    facebook_ok = bool(settings.page_access_token)
    if not facebook_ok:
        try:
            from repositories.social_account_repository import SocialAccountRepository
            repo = SocialAccountRepository()
            accounts = repo.find_by_platform("facebook")
            facebook_ok = any(
                getattr(a, 'enabled', False) and (
                    getattr(a, 'access_token', None) or
                    (getattr(a, 'platform_config', {}) or {}).get('page_access_token')
                )
                for a in (accounts or [])
            )
        except Exception:
            pass

    services = {
        "gemini": {"status": "ok" if settings.gemini_key else "error"},
        "facebook": {"status": "ok" if facebook_ok else "error"},
        "supabase": {"status": "ok" if settings.supabase_enabled else "degraded"},
    }
    all_healthy = all(s["status"] == "ok" for s in services.values())
    return JSONResponse({
        "status": "ok" if all_healthy else "degraded",
        "version": "12.1.0",
        "services": services,
    })


@router.get("/dashboard/live")
async def dashboard_live(_auth: bool = Depends(require_auth)):
    """Live dashboard stats for authenticated operators/workspace users."""
    from core.socket_server import build_live_stats
    return build_live_stats()


# ═══════════════════════════════════════════════════════════════════════════
# LOGS — LIVE STREAM
# ═══════════════════════════════════════════════════════════════════════════
#
# v-next: the endpoint now surfaces the LogBuffer's aggregate stats so the
# Live Logs page can display accurate "errors / warnings / total" tiles
# regardless of the current filter. The `search` and `module` parameters
# were also added so the "Filter & Regex Criteria" UI can actually filter
# the returned stream.
#
# Background: LogBuffer was never populated by anything before v15.3
# of main.py, which attached a logging.Handler that pushes every
# logger.info / warning / error record into LogBuffer.get_instance().
# This endpoint was always correct — it just had nothing to read.

@router.get("/logs/recent")
async def logs_recent(
    limit: int = 50,
    level: Optional[str] = None,
    module: Optional[str] = None,
    search: Optional[str] = None,
    _auth: bool = Depends(require_auth),
):
    """Get recent structured log events from the in-memory LogBuffer.

    No sensitive content is exposed here — the buffer stores log messages
    exactly as written (module name, level, message). Secrets must be
    redacted at the log call site, not at the reader.
    """
    from core.log_buffer import LogBuffer

    buf = LogBuffer.get_instance()

    # Normalize inputs. FastAPI sometimes sends empty strings for missing
    # query params; treat "" as None so the filter doesn't try to match
    # against an empty level and drop everything.
    resolved_level = (level or "").strip().upper() or None
    if resolved_level in ("ALL", ""):
        resolved_level = None
    resolved_module = (module or "").strip() or None
    resolved_search = (search or "").strip() or None

    entries = buf.get_recent(
        limit=min(max(int(limit), 1), 500),
        level=resolved_level,
        module=resolved_module,
        search=resolved_search,
    )

    # Aggregate stats reflect the WHOLE buffer, not the filtered slice.
    # This lets the UI show "12 errors" even when the filter is set to INFO.
    stats = buf.get_stats()

    return {
        "ok": True,
        "logs": entries,
        "count": len(entries),
        "stats": {
            "total_logs": stats.get("total_logs", 0),
            "buffered": stats.get("buffered", 0),
            "errors": stats.get("errors", 0),
            "warnings": stats.get("warnings", 0),
            "info": stats.get("info", 0),
            "uptime_seconds": stats.get("uptime_seconds", 0),
        },
        "filters": {
            "level": resolved_level,
            "module": resolved_module,
            "search": resolved_search,
        },
    }
