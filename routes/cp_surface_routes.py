"""Control-plane surface routes — bind UI paths that were previously client-side stubs.

Every endpoint talks to a real runtime object when available. Empty lists / unavailable
status are honest outcomes, never fabricated metrics.

v15.5 (2026-10-30) — dream system canonical resolution:
  The /dreams/* endpoints previously read only from the organism-scoped
  `org.dreams` instance, which is created in organism/lifecycle.py:80 but
  never started. The canonical DreamSystem lives at core.dreams.dream_system
  and is started by main.py's lifespan. These endpoints now prefer the
  canonical instance, falling back to the organism instance only if the
  canonical one is unavailable. Added POST /dreams/run for manual triggering
  outside the 1–5 AM UTC window.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.routes.cp_surface")
router = APIRouter(prefix="/api/v1", tags=["cp-surface"])

# In-process guardian blocklist (species-level operational control)
_blocklist: set[str] = set()


def _container_dep():
    from api.dependencies import get_container
    return get_container


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


def _resolve_dreams(request: Request):
    """Resolve the canonical DreamSystem, falling back to the organism instance.

    The canonical instance lives at core.dreams.dream_system and is started
    by main.py's lifespan. The organism-scoped instance (org.dreams) is created
    in organism/lifecycle.py but never started, so we prefer the canonical one.
    """
    try:
        from core.dreams import dream_system as canonical
        if canonical is not None:
            return canonical
    except Exception:
        pass
    org = _organism(request)
    return getattr(org, "dreams", None) if org else None


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
    return {"ok": True, "started": False, "message": "Guardian not configured — no scan started", "source": "none"}


# ── Reflexes ────────────────────────────────────────────────────────────────

@router.get("/reflexes/triggers", dependencies=[Depends(_auth_dep())])
async def reflex_triggers():
    try:
        from core.reflexes import ReflexSystem
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

async def _workspace_brand_ids(request: Request, container: Any) -> set[str]:
    """Resolve the authenticated workspace's brands before exposing engine records."""
    workspace_id = _workspace_id(request)
    if container is None:
        raise LookupError("workspace repository container unavailable")

    from api.dependencies import require_workspace_ownership
    from api.v1_helpers import _async_wrap
    from repositories.brand_repository import BrandRepository

    await require_workspace_ownership("workspace", workspace_id, request, container)
    repository = await _async_wrap(container.resolve, BrandRepository)
    brands = await _async_wrap(repository.find_by_workspace, workspace_id)
    brand_ids = {str(getattr(brand, "id", "")).strip() for brand in brands}
    brand_ids.discard("")
    return brand_ids


def _filter_workspace_condition_records(
    records: List[Dict[str, Any]],
    brand_ids: set[str],
    collection_name: str,
) -> List[Dict[str, Any]]:
    """Fail closed if a record cannot be tied to a workspace-owned brand."""
    if any(not str(record.get("brand_id") or "").strip() for record in records):
        raise ValueError(f"{collection_name} contains an unscoped record")
    return [record for record in records if str(record["brand_id"]) in brand_ids]


def _condition_records(engine: Any, method_name: str, attribute_name: str) -> List[Dict[str, Any]]:
    """Serialize the engine's actual keyed/list state without inventing empty data."""
    provider = getattr(engine, method_name, None)
    raw = provider() if callable(provider) else getattr(engine, attribute_name, None)
    if raw is None:
        raise LookupError(f"{attribute_name} provider unavailable")

    if isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        entries = [(None, item) for item in raw]
    else:
        raise TypeError(f"{attribute_name} has an unsupported collection shape")

    records: List[Dict[str, Any]] = []
    for key, item in entries:
        record = jsonable_encoder(item)
        if not isinstance(record, dict):
            raise TypeError(f"{attribute_name} contains a non-record value")
        if record.get("id") is None:
            if isinstance(key, tuple) and key:
                record["id"] = ":".join(str(part) for part in key)
                record.setdefault("brand_id", str(key[0]))
            elif key is not None:
                record["id"] = str(key)
            elif isinstance(record.get("metric"), str) and record["metric"].strip():
                record["id"] = record["metric"]
            else:
                raise ValueError(f"{attribute_name} contains a record without a stable identifier")
        records.append(record)
    return records


@router.get("/condition/baselines", dependencies=[Depends(_auth_dep())])
async def condition_baselines(request: Request, container=Depends(_container_dep())):
    try:
        from services.condition_engine import autonomous_engine
        brand_ids = await _workspace_brand_ids(request, container)
        baselines = _condition_records(autonomous_engine, "get_baselines", "_baselines")
        baselines = _filter_workspace_condition_records(baselines, brand_ids, "baselines")
        return {"ok": True, "baselines": baselines, "count": len(baselines), "source": "condition_engine"}
    except Exception as exc:
        logger.warning("condition baselines unavailable: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "baselines": [],
            "count": 0,
            "source": "error",
            "error": type(exc).__name__,
            "message": "Condition baseline evidence is unavailable.",
        }


@router.get("/condition/experiments", dependencies=[Depends(_auth_dep())])
async def condition_experiments(request: Request, container=Depends(_container_dep())):
    try:
        from services.condition_engine import autonomous_engine
        brand_ids = await _workspace_brand_ids(request, container)
        experiments = _condition_records(autonomous_engine, "get_experiments", "_experiments")
        experiments = _filter_workspace_condition_records(experiments, brand_ids, "experiments")
        return {"ok": True, "experiments": experiments, "count": len(experiments), "source": "condition_engine"}
    except Exception as exc:
        logger.warning("condition experiments unavailable: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "experiments": [],
            "count": 0,
            "source": "error",
            "error": type(exc).__name__,
            "message": "Condition experiment evidence is unavailable.",
        }


@router.get("/condition/decisions", dependencies=[Depends(_auth_dep())])
async def condition_decisions(request: Request, container=Depends(_container_dep())):
    try:
        from services.condition_engine import autonomous_engine
        brand_ids = await _workspace_brand_ids(request, container)
        decisions = _condition_records(autonomous_engine, "get_decisions", "_decisions")
        decisions = _filter_workspace_condition_records(decisions, brand_ids, "decisions")
        status = {}
        status_provider = getattr(autonomous_engine, "get_status", None)
        if callable(status_provider):
            try:
                value = status_provider()
                status = jsonable_encoder(value) if isinstance(value, dict) else {}
            except Exception as status_exc:
                logger.warning("condition engine status summary unavailable: %s", type(status_exc).__name__)
        return {
            "ok": True,
            "decisions": decisions,
            "count": len(decisions),
            "status": status,
            "source": "condition_engine",
        }
    except Exception as exc:
        logger.warning("condition decisions unavailable: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "decisions": [],
            "count": 0,
            "source": "error",
            "error": type(exc).__name__,
            "message": "Condition decision evidence is unavailable.",
        }


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
    """Status of the canonical DreamSystem.

    Prefers core.dreams.dream_system (started by main.py) over the organism-scoped
    instance (org.dreams), which is created in organism/lifecycle.py but never started.
    """
    dreams = None
    source = "none"

    try:
        from core.dreams import dream_system as canonical
        if canonical is not None:
            dreams = canonical
            source = "dream_system_canonical"
    except Exception:
        pass

    if dreams is None:
        org = _organism(request)
        dreams = getattr(org, "dreams", None) if org else None
        source = "organism_dreams" if dreams else "none"

    if dreams is None:
        return {
            "ok": True,
            "status": "unbound",
            "running": False,
            "dream_count": 0,
            "insights_count": 0,
            "last_dream_time": None,
            "recent_insights": [],
            "message": "No DreamSystem instance bound",
            "source": "none",
        }

    status = dreams.get_status() if hasattr(dreams, "get_status") else {
        "running": getattr(dreams, "_running", False),
        "dream_count": getattr(dreams, "_dream_count", 0),
        "insights_count": len(getattr(dreams, "_insights", []) or []),
        "last_dream_time": None,
        "recent_insights": list(getattr(dreams, "_insights", []) or [])[-5:],
    }

    return {
        "ok": True,
        "status": "active",
        **(status if isinstance(status, dict) else {}),
        "source": source,
    }


@router.post("/dreams/run", dependencies=[Depends(_operator_dep())])
async def trigger_dream_cycle(request: Request):
    """Force a single dream cycle for testing / manual recovery.

    The dream loop fires only between 1 AM and 5 AM UTC. This endpoint lets
    an operator trigger a dream outside the window without waiting.
    """
    dreams = _resolve_dreams(request)
    if dreams is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "dream_system_not_bound",
                "message": "No DreamSystem instance is bound. Check boot logs.",
            },
        )
    if not getattr(dreams, "_running", False):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "dream_system_not_running",
                "message": "DreamSystem is not running. Check main.py boot logs for start failure.",
            },
        )
    if not hasattr(dreams, "run_once"):
        raise HTTPException(
            status_code=501,
            detail={
                "error": "run_once_not_implemented",
                "message": "DreamSystem does not implement run_once().",
            },
        )
    result = await dreams.run_once()
    return {"ok": True, "status": result}


@router.get("/dreams/insights", dependencies=[Depends(_auth_dep())])
async def dreams_insights(request: Request):
    dreams = _resolve_dreams(request)
    insights = list(getattr(dreams, "_insights", []) or [])[-50:] if dreams else []
    return {
        "ok": True,
        "insights": insights,
        "count": len(insights),
        "source": "dream_system" if dreams else "none",
    }


@router.get("/dreams/learning-curve", dependencies=[Depends(_auth_dep())])
async def dreams_learning_curve(request: Request):
    dreams = _resolve_dreams(request)
    curve = []
    if dreams and hasattr(dreams, "get_learning_curve"):
        curve = dreams.get_learning_curve() or []
    elif dreams:
        insights = list(getattr(dreams, "_insights", []) or [])
        curve = [{"i": i, "count": 1} for i, _ in enumerate(insights[-30:])]
    return {
        "ok": True,
        "curve": curve,
        "points": curve,
        "count": len(curve),
        "source": "dream_system" if dreams else "none",
    }


@router.get("/dreams/patterns", dependencies=[Depends(_auth_dep())])
async def dreams_patterns(request: Request):
    dreams = _resolve_dreams(request)
    patterns = []
    if dreams and hasattr(dreams, "get_patterns"):
        patterns = dreams.get_patterns() or []
    elif dreams:
        patterns = list(getattr(dreams, "_patterns", []) or [])
    return {
        "ok": True,
        "patterns": patterns,
        "count": len(patterns),
        "source": "dream_system" if dreams else "none",
    }


@router.post("/dreams/insights/{insight_id}/apply", dependencies=[Depends(_operator_dep())])
async def apply_dream_insight(insight_id: str, request: Request):
    dreams = _resolve_dreams(request)
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
