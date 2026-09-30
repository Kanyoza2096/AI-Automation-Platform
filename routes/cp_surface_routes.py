"""Control-plane surface routes — bind UI paths that were previously client-side stubs.

Every endpoint talks to a real runtime object when available. Empty lists / unavailable
status are honest outcomes, never fabricated metrics.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.routes.cp_surface")
router = APIRouter(prefix="/api/v1", tags=["cp-surface"])

# In-process guardian blocklist (species-level operational control)
_blocklist: set[str] = set()


def _auth_dep():
    from api.v1 import require_auth
    return require_auth

def _operator_dep():
    from core.security.deps import require_workspace_operator
    return require_workspace_operator


def _workspace_id(request: Request) -> str:
    """Return the workspace bound by canonical authentication."""
    from core.request_context import require_request_context
    ctx = require_request_context()
    if not ctx.workspace_id:
        raise HTTPException(403, detail="Authenticated request has no workspace scope")
    return str(ctx.workspace_id)


def _organism(request: Request):
    wid = _workspace_id(request)
    if not wid:
        return None
    try:
        from core.organism import get_organism
        return get_organism(wid)
    except Exception:
        return None


# ── Pulse thresholds ────────────────────────────────────────────────────────

class PulseThresholdsBody(BaseModel):
    maxIdleSec: Optional[int] = Field(default=None, ge=1, le=86400)
    maxWriteLagMs: Optional[int] = Field(default=None, ge=1, le=3_600_000)


@router.put("/pulse/thresholds", dependencies=[Depends(_operator_dep())])
@router.post("/pulse/thresholds", dependencies=[Depends(_operator_dep())])
async def update_pulse_thresholds(body: PulseThresholdsBody):
    try:
        from core.universal_pulse import get_universal_pulse
        pulse = get_universal_pulse()
    except Exception:
        pulse = None
    if pulse is None:
        raise HTTPException(503, detail="Universal pulse not started")
    applied = {}
    if body.maxIdleSec is not None and hasattr(pulse, "max_idle_sec"):
        pulse.max_idle_sec = body.maxIdleSec
        applied["maxIdleSec"] = body.maxIdleSec
    if body.maxWriteLagMs is not None and hasattr(pulse, "max_write_lag_ms"):
        pulse.max_write_lag_ms = body.maxWriteLagMs
        applied["maxWriteLagMs"] = body.maxWriteLagMs
    if hasattr(pulse, "set_thresholds") and callable(pulse.set_thresholds):
        try:
            pulse.set_thresholds(**{k: v for k, v in body.model_dump().items() if v is not None})
        except Exception as exc:
            logger.warning("pulse set_thresholds failed: %s", type(exc).__name__)
    return {"ok": True, "applied": applied, "source": "universal_pulse"}


# ── Guardian blocklist ──────────────────────────────────────────────────────

@router.get("/guardian/blocklist", dependencies=[Depends(_auth_dep())])
async def get_blocklist():
    return {"ok": True, "ips": sorted(_blocklist), "count": len(_blocklist), "source": "process_blocklist"}


class BlocklistBody(BaseModel):
    ip: str = Field(..., min_length=3, max_length=64)


@router.post("/guardian/blocklist", dependencies=[Depends(_operator_dep())])
async def add_blocklist_ip(body: BlocklistBody):
    ip = body.ip.strip()
    if not ip:
        raise HTTPException(400, detail="ip required")
    _blocklist.add(ip)
    return {"ok": True, "ip": ip, "ips": sorted(_blocklist), "source": "process_blocklist"}


@router.delete("/guardian/blocklist/{ip}", dependencies=[Depends(_operator_dep())])
async def remove_blocklist_ip(ip: str):
    _blocklist.discard(ip)
    return {"ok": True, "ip": ip, "ips": sorted(_blocklist), "source": "process_blocklist"}


# ── Security scan → Guardian scan ───────────────────────────────────────────

@router.post("/security/scan", dependencies=[Depends(_operator_dep())])
async def security_scan(request: Request):
    """Alias onto Guardian scan when available."""
    try:
        from services.guardian_service import GuardianService
        container = getattr(request.app.state, "container", None)
        svc = None
        if container is not None:
            try:
                svc = container.try_resolve(GuardianService)
            except Exception:
                pass
        if svc is None:
            try:
                from services import guardian_service as gm
                svc = getattr(gm, "guardian_service", None) or getattr(gm, "GuardianService", None)
            except Exception:
                pass
        if svc is not None and hasattr(svc, "scan_all"):
            import asyncio
            asyncio.create_task(asyncio.to_thread(svc.scan_all) if not asyncio.iscoroutinefunction(svc.scan_all) else svc.scan_all())
            return {"ok": True, "started": True, "message": "Guardian scan started", "source": "guardian"}
    except Exception as exc:
        logger.warning("security scan: %s", type(exc).__name__)
    # Fall through: still acknowledge request path exists
    return {"ok": True, "started": False, "message": "Guardian not configured — no scan started", "source": "none"}


# ── Reflexes ────────────────────────────────────────────────────────────────

@router.get("/reflexes/triggers", dependencies=[Depends(_auth_dep())])
async def reflex_triggers():
    try:
        from core.reflexes import ReflexSystem
        # Prefer live instance if registered
        triggers: List[Dict[str, Any]] = []
        try:
            from core.service_registry import get_service_registry
            reg = get_service_registry()
            reflex = reg.get("reflex_system") if hasattr(reg, "get") else None
            if reflex is None:
                for name, svc in (reg.get_all() or {}).items():
                    if "reflex" in name.lower():
                        reflex = svc
                        break
            if reflex is not None:
                triggers = list(getattr(reflex, "_triggered_reflexes", []) or [])[-50:]
                return {
                    "ok": True,
                    "triggers": triggers,
                    "count": len(triggers),
                    "locked_down": bool(getattr(reflex, "_locked_down", False)),
                    "instant_block_actions": sorted(getattr(ReflexSystem, "INSTANT_BLOCK_ACTIONS", set()) or []),
                    "source": "reflex_system",
                }
        except Exception:
            pass
        return {
            "ok": True,
            "triggers": [],
            "count": 0,
            "instant_block_actions": sorted(getattr(ReflexSystem, "INSTANT_BLOCK_ACTIONS", set()) or []),
            "source": "reflex_system_static",
        }
    except Exception as exc:
        return {"ok": False, "triggers": [], "error": type(exc).__name__, "source": "none"}


# ── Condition surfaces ──────────────────────────────────────────────────────

@router.get("/condition/baselines", dependencies=[Depends(_auth_dep())])
async def condition_baselines(request: Request):
    try:
        from services.condition_engine import autonomous_engine
        engine = autonomous_engine
        baselines = []
        if hasattr(engine, "get_baselines"):
            baselines = engine.get_baselines() or []
        elif hasattr(engine, "_baselines"):
            raw = getattr(engine, "_baselines", {}) or {}
            if isinstance(raw, dict):
                baselines = [{"id": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in raw.items()]
            else:
                baselines = list(raw)
        return {"ok": True, "baselines": baselines, "count": len(baselines), "source": "condition_engine"}
    except Exception as exc:
        return {"ok": True, "baselines": [], "count": 0, "source": "none", "message": type(exc).__name__}


@router.get("/condition/experiments", dependencies=[Depends(_auth_dep())])
async def condition_experiments():
    try:
        from services.condition_engine import autonomous_engine
        engine = autonomous_engine
        experiments = []
        if hasattr(engine, "get_experiments"):
            experiments = engine.get_experiments() or []
        elif hasattr(engine, "_experiments"):
            experiments = list(getattr(engine, "_experiments", []) or [])
        return {"ok": True, "experiments": experiments, "count": len(experiments), "source": "condition_engine"}
    except Exception as exc:
        return {"ok": True, "experiments": [], "count": 0, "source": "none", "message": type(exc).__name__}


@router.get("/condition/decisions", dependencies=[Depends(_auth_dep())])
async def condition_decisions():
    try:
        from services.condition_engine import autonomous_engine
        engine = autonomous_engine
        decisions = []
        if hasattr(engine, "get_decisions"):
            decisions = engine.get_decisions() or []
        elif hasattr(engine, "_decision_history"):
            decisions = list(getattr(engine, "_decision_history", []) or [])[-50:]
        status = engine.get_status() if hasattr(engine, "get_status") else {}
        return {"ok": True, "decisions": decisions, "count": len(decisions), "status": status, "source": "condition_engine"}
    except Exception as exc:
        return {"ok": True, "decisions": [], "count": 0, "source": "none", "message": type(exc).__name__}


# ── Predictions optimal times ───────────────────────────────────────────────

@router.get("/predictions/optimal-times", dependencies=[Depends(_auth_dep())])
async def optimal_posting_times(request: Request):
    try:
        from services.condition_engine import autonomous_engine
        if hasattr(autonomous_engine, "get_optimal_times"):
            times = autonomous_engine.get_optimal_times() or []
            return {"ok": True, "times": times, "source": "condition_engine"}
    except Exception:
        pass
    # Honest default schedule slots (not engagement scores) — labeled as policy defaults
    return {
        "ok": True,
        "times": [
            {"hour_local": 9, "label": "morning", "source": "policy_default"},
            {"hour_local": 12, "label": "midday", "source": "policy_default"},
            {"hour_local": 18, "label": "evening", "source": "policy_default"},
        ],
        "source": "policy_default",
        "message": "No learned optimal-time model bound; returning policy default slots.",
    }


# ── Content candidates / queue ──────────────────────────────────────────────

@router.get("/content/candidates", dependencies=[Depends(_auth_dep())])
async def content_candidates(request: Request, limit: int = 20):
    rows: List[Dict[str, Any]] = []
    try:
        from core.service_registry import get_service_registry
        reg = get_service_registry()
        for name, svc in (reg.get_all() or {}).items():
            if "content" in name.lower() or "autonomous_content" in name.lower():
                if hasattr(svc, "list_candidates"):
                    rows = list(svc.list_candidates(limit=limit) or [])
                    break
                if hasattr(svc, "get_queue"):
                    rows = list(svc.get_queue() or [])[:limit]
                    break
    except Exception:
        pass
    if not rows:
        try:
            # Fall back to recent post_history as observational candidates
            from config.settings import settings
            if getattr(settings, "supabase_enabled", False):
                from core.supabase_client import get_supabase
                sb = get_supabase()
                if sb:
                    res = sb.table("post_history").select("*").order("published_at", desc=True).limit(limit).execute()
                    rows = list(res.data or [])
        except Exception:
            pass
    return {"ok": True, "candidates": rows, "items": rows, "count": len(rows), "source": "content_or_history"}


@router.get("/content/queue", dependencies=[Depends(_auth_dep())])
async def content_queue(request: Request, limit: int = 20):
    try:
        from core.service_registry import get_service_registry
        reg = get_service_registry()
        for name, svc in (reg.get_all() or {}).items():
            if "content" in name.lower() or "queue" in name.lower():
                if hasattr(svc, "get_queue"):
                    q = list(svc.get_queue() or [])[:limit]
                    return {"ok": True, "queue": q, "count": len(q), "source": name}
    except Exception:
        pass
    # Jobs pending as operational queue
    try:
        from core.service_registry import get_service_registry
        # soft empty
    except Exception:
        pass
    return {"ok": True, "queue": [], "count": 0, "source": "none", "message": "No content queue provider bound"}


class TopicBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    category: Optional[str] = None


@router.post("/content/candidates", dependencies=[Depends(_operator_dep())])
async def generate_topic_candidate(body: TopicBody, request: Request):
    """Stage a topic candidate — does not publish live social content."""
    candidate = {
        "id": f"cand_{abs(hash(body.prompt)) % 10_000_000}",
        "prompt": body.prompt,
        "category": body.category or "general",
        "status": "staged",
        "source": "control_plane",
    }
    # Try autonomous content if available
    try:
        from core.service_registry import get_service_registry
        reg = get_service_registry()
        for name, svc in (reg.get_all() or {}).items():
            if hasattr(svc, "stage_candidate"):
                result = svc.stage_candidate(body.prompt, category=body.category)
                if isinstance(result, dict):
                    return {"ok": True, "candidate": result, "source": name}
    except Exception as exc:
        logger.warning("stage_candidate: %s", type(exc).__name__)
    return {"ok": True, "candidate": candidate, "source": "staged_local", "message": "Staged in response only; no durable store bound"}


@router.post("/content/candidates/{candidate_id}/publish", dependencies=[Depends(_operator_dep())])
async def publish_content_topic(candidate_id: str, request: Request):
    """Refuse silent live publish — require governed publish.dry_run or workflow path."""
    return {
        "ok": False,
        "accepted": False,
        "candidate_id": candidate_id,
        "message": "Live publish is only available via governed command publish.dry_run or workflow publish path.",
        "use_command": "publish.dry_run",
        "source": "policy",
    }


# ── Dreams ──────────────────────────────────────────────────────────────────

@router.get("/dreams/status", dependencies=[Depends(_auth_dep())])
async def dreams_status(request: Request):
    org = _organism(request)
    dreams = getattr(org, "dreams", None) if org else None
    if dreams is None:
        try:
            from core.dreams import DreamSystem
            return {"ok": True, "status": "unbound", "message": "No organism dreams instance for workspace", "source": "none"}
        except Exception:
            return {"ok": True, "status": "unavailable", "source": "none"}
    status = dreams.get_status() if hasattr(dreams, "get_status") else {
        "dream_count": getattr(dreams, "_dream_count", 0),
        "insights": len(getattr(dreams, "_insights", []) or []),
    }
    return {"ok": True, "status": "active", **(status if isinstance(status, dict) else {}), "source": "dream_system"}


@router.get("/dreams/insights", dependencies=[Depends(_auth_dep())])
async def dreams_insights(request: Request):
    org = _organism(request)
    dreams = getattr(org, "dreams", None) if org else None
    insights = list(getattr(dreams, "_insights", []) or [])[-50:] if dreams else []
    return {"ok": True, "insights": insights, "count": len(insights), "source": "dream_system" if dreams else "none"}


@router.get("/dreams/learning-curve", dependencies=[Depends(_auth_dep())])
async def dreams_learning_curve(request: Request):
    org = _organism(request)
    dreams = getattr(org, "dreams", None) if org else None
    curve = []
    if dreams and hasattr(dreams, "get_learning_curve"):
        curve = dreams.get_learning_curve() or []
    elif dreams:
        insights = list(getattr(dreams, "_insights", []) or [])
        curve = [{"i": i, "count": 1} for i, _ in enumerate(insights[-30:])]
    return {"ok": True, "curve": curve, "points": curve, "count": len(curve), "source": "dream_system" if dreams else "none"}


@router.get("/dreams/patterns", dependencies=[Depends(_auth_dep())])
async def dreams_patterns(request: Request):
    org = _organism(request)
    dreams = getattr(org, "dreams", None) if org else None
    patterns = []
    if dreams and hasattr(dreams, "get_patterns"):
        patterns = dreams.get_patterns() or []
    elif dreams:
        patterns = list(getattr(dreams, "_patterns", []) or [])
    return {"ok": True, "patterns": patterns, "count": len(patterns), "source": "dream_system" if dreams else "none"}


@router.post("/dreams/insights/{insight_id}/apply", dependencies=[Depends(_operator_dep())])
async def apply_dream_insight(insight_id: str, request: Request):
    org = _organism(request)
    dreams = getattr(org, "dreams", None) if org else None
    if dreams is None:
        raise HTTPException(503, detail="Dream system not bound for workspace")
    if hasattr(dreams, "apply_insight"):
        result = dreams.apply_insight(insight_id)
        return {"ok": True, "result": result, "source": "dream_system"}
    return {
        "ok": True,
        "applied": False,
        "insight_id": insight_id,
        "message": "DreamSystem has no apply_insight method; insight acknowledged only",
        "source": "dream_system",
    }


# ── Heartbeat ───────────────────────────────────────────────────────────────

@router.get("/heartbeat/status", dependencies=[Depends(_auth_dep())])
async def heartbeat_status():
    try:
        from core.heartbeat import heartbeat
        if hasattr(heartbeat, "get_status"):
            return {"ok": True, **heartbeat.get_status(), "source": "heartbeat"}
        return {
            "ok": True,
            "running": bool(getattr(heartbeat, "_running", False) or getattr(heartbeat, "running", False)),
            "source": "heartbeat",
        }
    except Exception as exc:
        return {"ok": False, "status": "unavailable", "error": type(exc).__name__, "source": "none"}


@router.get("/heartbeat/services", dependencies=[Depends(_auth_dep())])
async def heartbeat_services():
    services = []
    try:
        from core.service_registry import get_service_registry
        reg = get_service_registry()
        for name in reg.get_names():
            services.append({"id": name, "name": name, "status": "registered"})
    except Exception:
        pass
    return {"ok": True, "services": services, "count": len(services), "source": "service_registry"}


@router.get("/heartbeat/history", dependencies=[Depends(_auth_dep())])
async def heartbeat_history(limit: int = 50):
    history = []
    try:
        from core.heartbeat import heartbeat
        hist = getattr(heartbeat, "_history", None) or getattr(heartbeat, "history", None) or []
        if callable(hist):
            hist = hist()
        history = list(hist)[-limit:]
    except Exception:
        pass
    return {"ok": True, "history": history, "count": len(history), "source": "heartbeat"}



@router.get("/social/twitter/status", dependencies=[Depends(_auth_dep())])
async def twitter_provider_status():
    """Readiness for X/Twitter provider — not a browser OAuth redirect."""
    try:
        from config.settings import settings
        has_key = bool(getattr(settings, "twitter_api_key", "") or "")
        has_secret = bool(getattr(settings, "twitter_api_secret", "") or "")
        has_access = bool(getattr(settings, "twitter_access_token", "") or "")
        has_access_secret = bool(getattr(settings, "twitter_access_secret", "") or "")
        ready = has_key and has_secret and has_access and has_access_secret
        return {
            "ok": True,
            "platform": "twitter",
            "provider_ready": ready,
            "configured": {
                "api_key": has_key,
                "api_secret": has_secret,
                "access_token": has_access,
                "access_secret": has_access_secret,
            },
            "message": (
                "Species-level Twitter credentials present — publish path can use services.twitter"
                if ready
                else "Missing Twitter env credentials — register per-workspace accounts or set TWITTER_* settings"
            ),
            "oauth_browser": False,
            "note": "Browser OAuth redirect is not enabled; use Social Accounts registration or env credentials.",
            "source": "settings",
        }
    except Exception as exc:
        return {"ok": False, "platform": "twitter", "provider_ready": False, "error": type(exc).__name__}



def register_cp_surface_routes(app) -> None:
    app.include_router(router)
