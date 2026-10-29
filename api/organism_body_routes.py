"""
Phase E — Body visible.

Read-only API that surfaces the living organism:

  Vitals  — pulse, heartbeats, bus health, metabolism remaining
  Mind    — director proposals, council decisions, conditions
  Hands   — jobs, publishes, reflex blocks
  Sleep   — dream insights
  Body    — combined snapshot for one dashboard poll

All data comes from UnifiedEventBus store, OrganismRegistry, SynapseMatrix,
DecisionGate, Metabolism — not from fake fixtures.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

logger = logging.getLogger("kanyoza.api.organism_body")

router = APIRouter(prefix="/api/v1", tags=["organism-body"])

# Canonical event buckets
VITALS_EVENTS = {
    "heartbeat.beat", "service_heartbeat",
    "pulse.stagnant", "pulse.cleared", "pulse.report",
    "circulation.blockage", "species.boot", "organism.born",
}
MIND_EVENTS = {
    "condition.changed", "brand_health_updated", "engagement_data",
    "director.proposal", "director.briefing", "action.proposed",
    "publish_request", "council.decided", "intelligent_decision",
}
HANDS_EVENTS = {
    "post.published", "post_published",
    "job.completed", "job.failed", "task_completed", "task_failed",
    "content.failed", "content_failed",
    "reflex.fired", "reflex.triggered",
}
SLEEP_EVENTS = {
    "dream.insight", "memory.stored", "learning_opportunity", "pattern_discovered",
}


def _auth():
    from api.v1 import require_auth
    return require_auth


def _resolve_workspace(request: Request, workspace_id: Optional[str]) -> str:
    identity = getattr(request.state, "auth_identity", None)
    if identity is None:
        raise HTTPException(401, detail="Authentication required")
    try:
        from core.tenant_guard import assert_workspace_access
        if workspace_id:
            return assert_workspace_access(identity, workspace_id)
        bound = getattr(identity, "workspace_id", None)
        if bound:
            return assert_workspace_access(identity, bound)
        if getattr(identity, "is_species", False):
            from core.workspace_context import get_current_workspace
            current = get_current_workspace()
            if current:
                return assert_workspace_access(identity, current)
    except (PermissionError, HTTPException) as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(403, detail=str(exc)) from exc
    raise HTTPException(403, detail="No workspace bound to credentials")


def _get_bus():
    try:
        from core.unified_bus import get_bus_sync
        bus = get_bus_sync()
        if bus is not None:
            return bus
    except Exception:
        pass
    return None


def _filter_events(
    bus,
    event_names: set,
    workspace_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    if bus is None:
        return []
    try:
        entries = bus.replay_events(event_names=list(event_names), limit=max(limit * 3, 100))
    except Exception:
        entries = list(getattr(bus, "_event_store", []))[-limit * 3:]

    out: List[Dict[str, Any]] = []
    for e in reversed(list(entries)):
        name = getattr(e, "event_name", None) or (e.get("event_name") if isinstance(e, dict) else None)
        payload = getattr(e, "payload", None) or (e.get("payload") if isinstance(e, dict) else {}) or {}
        if name not in event_names:
            continue
        ws = None
        if isinstance(payload, dict):
            ws = payload.get("workspace_id") or (payload.get("_nerve") or {}).get("workspace_id")
        # platform + matching workspace + unbound (legacy)
        if ws and ws not in (workspace_id, "platform", "unbound") and workspace_id != "platform":
            continue
        out.append({
            "event_name": name,
            "event_id": getattr(e, "event_id", None),
            "correlation_id": getattr(e, "correlation_id", None) or (
                payload.get("correlation_id") if isinstance(payload, dict) else None
            ),
            "source": getattr(e, "source_engine", None),
            "published_at": (
                getattr(e, "published_at", None).isoformat()
                if getattr(e, "published_at", None) is not None
                and hasattr(getattr(e, "published_at", None), "isoformat")
                else str(getattr(e, "published_at", "") or "")
            ),
            "payload": _safe_payload(payload),
        })
        if len(out) >= limit:
            break
    return out


def _safe_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    scrub = {}
    for k, v in payload.items():
        if k in ("access_token", "token", "password", "secret", "encrypted_credential"):
            continue
        if isinstance(v, (str, int, float, bool, type(None))):
            scrub[k] = v
        elif isinstance(v, (list, dict)):
            scrub[k] = v  # nested already from our organs
        else:
            scrub[k] = str(v)[:200]
    return scrub


def _bus_health(bus) -> Dict[str, Any]:
    if bus is None:
        return {"status": "unavailable", "stats": {}}
    try:
        return bus.health_check()
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200]}


def _organism_vitals(workspace_id: str) -> Dict[str, Any]:
    try:
        from core.organism import organism_registry
        org = organism_registry.get(workspace_id)
        if org:
            return org.vitals()
        return {
            "workspace_id": workspace_id,
            "running": False,
            "life_status": "NOT_BORN",
            "life_status_basis": "no_runtime_registered",
            "status": "unknown",
            "health": "unknown",
            "note": "organism runtime has not been registered for this workspace",
        }
    except Exception as exc:
        return {"workspace_id": workspace_id, "error": str(exc)[:200]}


def _metabolism_status(workspace_id: str) -> Dict[str, Any]:
    try:
        from core.metabolism import metabolism
        return metabolism.status(workspace_id)
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _synapse_stats() -> Dict[str, Any]:
    try:
        from core.synapses import synapse_matrix
        return synapse_matrix.get_stats()
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _require_auth_dep():
    from api.v1 import require_auth
    return require_auth


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/organism/vitals")
async def organism_vitals(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Pulse, heartbeats, bus health, metabolism remaining."""
    ws = _resolve_workspace(request, workspace_id)
    bus = _get_bus()
    events = _filter_events(bus, VITALS_EVENTS, ws, limit=limit)
    slo = None
    try:
        from core.slo_metrics import compute_workspace_slos
        slo = compute_workspace_slos(ws)
    except Exception:
        pass
    return {
        "section": "vitals",
        "workspace_id": ws,
        "at": datetime.now(timezone.utc).isoformat(),
        "organism": _organism_vitals(ws),
        "metabolism": _metabolism_status(ws),
        "bus": _bus_health(bus),
        "synapses": _synapse_stats(),
        "slo": slo,
        "recent_events": events,
    }


@router.get("/organism/mind")
async def organism_mind(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(40, ge=1, le=200),
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Director proposals, council decisions, condition changes."""
    ws = _resolve_workspace(request, workspace_id)
    bus = _get_bus()
    events = _filter_events(bus, MIND_EVENTS, ws, limit=limit)
    decisions = [e for e in events if e["event_name"] in ("council.decided", "intelligent_decision")]
    proposals = [e for e in events if e["event_name"] in ("director.proposal", "director.briefing", "action.proposed")]
    conditions = [e for e in events if e["event_name"] in ("condition.changed", "brand_health_updated", "engagement_data")]
    return {
        "section": "mind",
        "workspace_id": ws,
        "at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "decisions": len(decisions),
            "proposals": len(proposals),
            "conditions": len(conditions),
        },
        "recent_decisions": decisions[:20],
        "recent_proposals": proposals[:20],
        "recent_conditions": conditions[:20],
        "recent_events": events,
    }


@router.get("/organism/hands")
async def organism_hands(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(40, ge=1, le=200),
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Publishes, jobs, reflex firings — the hands that act in the world."""
    ws = _resolve_workspace(request, workspace_id)
    bus = _get_bus()
    events = _filter_events(bus, HANDS_EVENTS, ws, limit=limit)
    publishes = [e for e in events if "published" in e["event_name"]]
    failures = [e for e in events if "fail" in e["event_name"]]
    reflexes = [e for e in events if "reflex" in e["event_name"]]
    return {
        "section": "hands",
        "workspace_id": ws,
        "at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "publishes": len(publishes),
            "failures": len(failures),
            "reflexes": len(reflexes),
        },
        "recent_publishes": publishes[:20],
        "recent_failures": failures[:20],
        "recent_reflexes": reflexes[:20],
        "recent_events": events,
    }


@router.get("/organism/sleep")
async def organism_sleep(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Dream insights and memory consolidations."""
    ws = _resolve_workspace(request, workspace_id)
    bus = _get_bus()
    events = _filter_events(bus, SLEEP_EVENTS, ws, limit=limit)
    dreams = [e for e in events if "dream" in e["event_name"] or "learning" in e["event_name"] or "pattern" in e["event_name"]]
    memories = [e for e in events if "memory" in e["event_name"]]
    org = _organism_vitals(ws)
    return {
        "section": "sleep",
        "workspace_id": ws,
        "at": datetime.now(timezone.utc).isoformat(),
        "organism_dreams_count": (org.get("stats") or {}).get("dreams", 0),
        "counts": {"dreams": len(dreams), "memories": len(memories)},
        "recent_dreams": dreams[:20],
        "recent_memories": memories[:20],
        "recent_events": events,
    }


@router.get("/organism/body")
async def organism_body(
    request: Request,
    workspace_id: Optional[str] = Query(None),
    limit: int = Query(15, ge=1, le=50),
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """One-shot full body snapshot for the dashboard."""
    ws = _resolve_workspace(request, workspace_id)
    bus = _get_bus()
    return {
        "workspace_id": ws,
        "at": datetime.now(timezone.utc).isoformat(),
        "vitals": {
            "organism": _organism_vitals(ws),
            "metabolism": _metabolism_status(ws),
            "bus": _bus_health(bus),
            "synapses": _synapse_stats(),
            "recent": _filter_events(bus, VITALS_EVENTS, ws, limit=limit),
        },
        "mind": {
            "recent": _filter_events(bus, MIND_EVENTS, ws, limit=limit),
        },
        "hands": {
            "recent": _filter_events(bus, HANDS_EVENTS, ws, limit=limit),
        },
        "sleep": {
            "recent": _filter_events(bus, SLEEP_EVENTS, ws, limit=limit),
        },
    }


@router.get("/organism/census")
async def organism_census(
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Species operator: list all living organisms in this process."""
    identity = getattr(request.state, "auth_identity", None)
    if not identity or not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Species operator required")
    try:
        from core.organism import organism_registry
        return {
            "count": len(organism_registry.list_ids()),
            "organisms": organism_registry.census(),
            "at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        raise HTTPException(500, detail=str(exc))


@router.get("/species/scale")
async def species_scale_status(
    request: Request,
    _auth: bool = Depends(lambda: __import__("api.v1", fromlist=["require_auth"]).require_auth),
):
    """Process role + environment + census size (species operators see full)."""
    from core.species_scale import role_info, environment_name, is_production
    identity = getattr(request.state, "auth_identity", None)
    info = {
        **role_info(),
        "environment": environment_name(),
        "is_production": is_production(),
    }
    if identity and getattr(identity, "is_species", False):
        try:
            from core.organism import organism_registry
            info["census_count"] = len(organism_registry.list_ids())
        except Exception:
            info["census_count"] = None
    return info
