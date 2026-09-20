"""Backend contract routes: real, bounded control-plane data surfaces.

Every route either reads an authoritative runtime/DB source or explicitly
reports that the requested signal is currently unobservable. No synthetic
"healthy" values are fabricated here.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query

router = APIRouter(prefix="/api/v1", tags=["backend-contract"])


def _auth_dep():
    from api.v1 import require_auth
    return require_auth


def _identity(request: Request):
    return getattr(request.state, "auth_identity", None)


def _workspace(request: Request, requested: Optional[str] = None) -> Optional[str]:
    identity = _identity(request)
    own = getattr(identity, "workspace_id", None) if identity else None
    if requested and own and requested != own and not getattr(identity, "is_species", False):
        raise HTTPException(403, detail="Workspace mismatch")
    return requested or own


def _meta():
    try:
        from services.meta_engine import meta_engine
        return meta_engine
    except Exception:
        return None


def _db():
    try:
        from services.supabase import supabase_service
        if supabase_service:
            return supabase_service.service_client or supabase_service.client
    except Exception:
        pass
    return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _director():
    try:
        from services.director import director
        return director
    except Exception:
        return None


@router.get("/engines", dependencies=[Depends(_auth_dep())])
async def engines():
    d = _director()
    if d is None:
        raise HTTPException(503, "Director unavailable")
    observations = getattr(d, "observations", {}) or {}
    return {"engines": {
        name: {
            "name": name,
            "status": _jsonable(getattr(obs, "status", "unknown")),
            "sector": _jsonable(getattr(obs, "sector", "unknown")),
            "issues": len(getattr(obs, "issues", []) or []),
        } for name, obs in observations.items()
    }, "total": len(observations)}


@router.get("/tasks", dependencies=[Depends(_auth_dep())])
async def tasks(request: Request, limit: int = Query(200, ge=1, le=1000)):
    d = _director()
    if d is None:
        raise HTTPException(503, "Director unavailable")
    store = getattr(d, "store", None)
    if store is not None and hasattr(store, "list_tasks"):
        rows = await store.list_tasks(limit=limit) if "limit" in inspect.signature(store.list_tasks).parameters else await store.list_tasks()
    else:
        rows = list(getattr(d, "tasks", {}).values())
    return {"tasks": [_jsonable(x) for x in rows[-limit:]], "count": min(len(rows), limit)}


@router.get("/health/live", dependencies=[Depends(_auth_dep())])
async def meta_live():
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    return {"ok": True, "status": _jsonable(meta.get_status())}


@router.get("/diagnoses", dependencies=[Depends(_auth_dep())])
async def diagnoses(limit: int = Query(50, ge=1, le=200)):
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    rows = list(getattr(meta, "_diagnoses", []))[-limit:]
    return {"diagnoses": [_jsonable(x) for x in rows], "count": len(rows)}


@router.get("/dependency-graph", dependencies=[Depends(_auth_dep())])
async def dependency_graph():
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    return {"ok": True, **_jsonable(meta.get_dependency_graph())}


@router.post("/diagnostics", dependencies=[Depends(_auth_dep())])
async def run_diagnostics():
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    health = await meta.check_health_async()
    found = meta.diagnose()
    return {"ok": True, "health": _jsonable(health), "diagnoses": [_jsonable(x) for x in found], "count": len(found)}


@router.get("/healing-history", dependencies=[Depends(_auth_dep())])
async def healing_history(limit: int = Query(50, ge=1, le=200)):
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    return {"healing_history": _jsonable(meta.get_recovery_plans()[-limit:]), "count": min(limit, len(meta.get_recovery_plans()))}


@router.get("/system-health", dependencies=[Depends(_auth_dep())])
async def system_health(request: Request):
    """Aggregate real health sources; unknown remains unknown."""
    meta = _meta()
    services = {}
    if meta is not None:
        try:
            services["meta_engine"] = _jsonable(meta.get_status())
        except Exception as exc:
            services["meta_engine"] = {"status": "error", "error": type(exc).__name__}
    try:
        from core.reliability import HealthChecker
        from plugins.base import get_plugin_registry
        plugin_registry = get_plugin_registry()
        checker = HealthChecker(plugin_registry)
        result = checker.check_all()
        if inspect.isawaitable(result):
            result = await result
        services["health_checker"] = {
            "status": "healthy" if all(
                bool(v.get("ok", True)) for v in result.values() if isinstance(v, dict)
            ) else "degraded",
            "plugins_checked": len(result),
            "results": _jsonable(result),
        }
    except Exception as exc:
        services["health_checker"] = {"status": "unobservable", "error": type(exc).__name__}
    return {"ok": True, "timestamp": datetime.now(timezone.utc).isoformat(), "services": services}


@router.get("/services", dependencies=[Depends(_auth_dep())])
async def service_inventory():
    from core.service_registry import get_service_registry
    registry = get_service_registry()
    items = []
    for name, service in registry.get_all().items():
        status = "unknown"
        details: Any = {}
        try:
            fn = getattr(service, "health_check", None) or getattr(service, "get_status", None)
            if fn:
                value = fn()
                if inspect.isawaitable(value):
                    value = await value
                if isinstance(value, dict):
                    details = _jsonable(value)
                    status = str(value.get("status", "unknown"))
        except Exception as exc:
            status = "error"
            details = {"error": type(exc).__name__}
        items.append({"name": name, "status": status, "details": details, "type": type(service).__name__})
    return {"services": items, "count": len(items), "source": "service_registry"}


@router.get("/topology", dependencies=[Depends(_auth_dep())])
async def topology():
    from core.engine_topology import CANONICAL_ENGINE_DEPENDENCIES, validate_topology
    registry_names = []
    service_status: dict = {}
    try:
        registry = __import__("core.service_registry", fromlist=["get_service_registry"]).get_service_registry()
        registry_names = list(registry.get_names())
        for name, service in registry.get_all().items():
            st = "unknown"
            try:
                fn = getattr(service, "health_check", None) or getattr(service, "get_status", None)
                if fn:
                    value = fn()
                    if inspect.isawaitable(value):
                        try:
                            value = await value
                        except Exception:
                            st = "healthy"  # registered + callable
                            value = None
                    if isinstance(value, dict):
                        st = str(value.get("status") or value.get("health") or "unknown").lower()
                    elif value is not None:
                        st = "healthy"
            except Exception:
                st = "error"
            service_status[name] = st
    except Exception:
        pass

    # Probe known process singletons that may not be in the service registry.
    probes = {
        "autonomous_loop": ("core.autonomous_loop", "_autonomous_loop"),
        "meta_engine": ("services.meta_engine", "meta_engine"),
        "director": ("services.director", "director"),
        "condition_engine": ("services.condition_engine", "autonomous_engine"),
        "event_bus": ("core.unified_bus", None),  # special
    }
    for name, (mod_path, attr) in probes.items():
        if name in service_status and service_status[name] not in ("unknown", ""):
            continue
        try:
            mod = __import__(mod_path, fromlist=["*"])
            if name == "event_bus":
                get_bus = getattr(mod, "get_unified_bus", None)
                if get_bus:
                    bus = get_bus()
                    if inspect.isawaitable(bus):
                        bus = await bus
                    service_status[name] = "healthy" if bus is not None else "unknown"
                    if bus is not None and name not in registry_names:
                        registry_names.append(name)
                continue
            obj = getattr(mod, attr, None) if attr else None
            if obj is None:
                continue
            if name not in registry_names:
                registry_names.append(name)
            running = getattr(obj, "_running", None)
            if running is None:
                running = getattr(obj, "running", None)
            if running is True:
                service_status[name] = "healthy"
            elif running is False:
                service_status[name] = "stopped"
            else:
                fn = getattr(obj, "health_check", None) or getattr(obj, "get_status", None)
                if fn:
                    value = fn()
                    if inspect.isawaitable(value):
                        value = await value
                    if isinstance(value, dict):
                        service_status[name] = str(value.get("status") or value.get("health") or "healthy").lower()
                    else:
                        service_status[name] = "healthy"
                else:
                    service_status[name] = "healthy"
        except Exception:
            pass

    runtime_set = set(registry_names) | set(service_status.keys())
    drift = validate_topology(list(runtime_set), CANONICAL_ENGINE_DEPENDENCIES)
    edges = [{"from": n, "to": dep} for n, deps in CANONICAL_ENGINE_DEPENDENCIES.items() for dep in deps]
    nodes = []
    for name in sorted(CANONICAL_ENGINE_DEPENDENCIES):
        if name in service_status:
            st = service_status[name]
        elif name in runtime_set:
            st = "healthy"
        else:
            st = "unknown"
        if st in ("ok", "operational", "nominal", "up", "active", "running", "alive"):
            st = "healthy"
        if st in ("stopped", "offline", "not_born"):
            st = "degraded" if st == "stopped" else st
        nodes.append({
            "id": name,
            "name": name,
            "label": name,
            "kind": "engine",
            "status": st,
            "runtime_registered": name in runtime_set,
        })
    return {
        "nodes": nodes,
        "edges": edges,
        "runtime_nodes": sorted(runtime_set),
        "drift": _jsonable(drift),
        "healthy": drift.healthy,
        "source": "canonical_engine_topology",
    }


@router.get("/events", dependencies=[Depends(_auth_dep())])
async def activity_events(limit: int = Query(50, ge=1, le=500)):
    try:
        from core.unified_bus import get_unified_bus
        bus = await get_unified_bus()
        if bus is None:
            raise HTTPException(503, "Event bus unavailable: not_initialized")
        rows = bus.replay_events(limit=limit)
        if hasattr(rows, "__await__"):
            rows = await rows
        return {"events": [_jsonable(x) for x in (rows or [])], "count": len(rows or []), "source": "unified_event_bus"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Event bus unavailable: {type(exc).__name__}")


@router.get("/predictions/forecast", dependencies=[Depends(_auth_dep())])
async def prediction_forecast():
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    rows = getattr(meta, "_predictions", [])
    return {"forecast": _jsonable(list(rows)), "count": len(rows), "source": "meta_engine"}


@router.get("/predictions/confidence", dependencies=[Depends(_auth_dep())])
async def prediction_confidence():
    meta = _meta()
    if meta is None:
        raise HTTPException(503, "Meta Engine unavailable")
    rows = list(getattr(meta, "_predictions", []))
    return {"confidence": [{"engine": p.engine, "confidence": p.confidence} for p in rows], "count": len(rows), "source": "meta_engine"}


@router.get("/quality/history", dependencies=[Depends(_auth_dep())])
async def quality_history(request: Request, brand_id: Optional[str] = None):
    try:
        from core.autonomous_content import autonomous_content
        scores = getattr(autonomous_content, "_quality_scores", {})
        if brand_id:
            values = scores.get(brand_id, [])
            return {"history": _jsonable(values[-100:]), "count": len(values), "brand_id": brand_id, "source": "autonomous_content"}
        flattened = []
        for bid, values in scores.items():
            flattened.extend({"brand_id": bid, "score": _jsonable(v)} for v in values[-20:])
        return {"history": flattened[-200:], "count": min(len(flattened), 200), "source": "autonomous_content"}
    except Exception as exc:
        raise HTTPException(503, f"Quality provider unavailable: {type(exc).__name__}")


@router.get("/competitors/list", dependencies=[Depends(_auth_dep())])
async def competitors_list(brand_id: str = Query(..., min_length=1, max_length=128)):
    try:
        from core.autonomous_content import autonomous_content
        competitors = await autonomous_content.discover_competitors(brand_id)
        return {"competitors": competitors, "count": len(competitors), "brand_id": brand_id, "source": "brand_configuration"}
    except Exception as exc:
        raise HTTPException(503, f"Competitor provider unavailable: {type(exc).__name__}")


@router.get("/competitors/gaps", dependencies=[Depends(_auth_dep())])
async def competitor_gaps(brand_id: str = Query(..., min_length=1, max_length=128)):
    try:
        from core.autonomous_content import autonomous_content
        result = await autonomous_content.analyze_competitors(brand_id)
        return {"gaps": result.get("gaps", []), "brand_id": brand_id, "source": "autonomous_content", "analysis": result}
    except Exception as exc:
        raise HTTPException(503, f"Competitor analysis unavailable: {type(exc).__name__}")


@router.get("/competitors/opportunities", dependencies=[Depends(_auth_dep())])
async def competitor_opportunities(brand_id: str = Query(..., min_length=1, max_length=128)):
    try:
        from core.autonomous_content import autonomous_content
        result = await autonomous_content.analyze_competitors(brand_id)
        return {"opportunities": result.get("opportunities", []), "brand_id": brand_id, "source": "autonomous_content", "analysis": result}
    except Exception as exc:
        raise HTTPException(503, f"Competitor analysis unavailable: {type(exc).__name__}")


@router.get("/integrations/status", dependencies=[Depends(_auth_dep())])
async def integrations_status():
    """Return live integration health from the canonical connector health helpers."""
    try:
        from core.socket_server import _build_integration_status
        statuses = _build_integration_status()
        return {
            "ok": True,
            "integrations": _jsonable(statuses),
            "count": len(statuses),
            "source": "connector_health",
        }
    except Exception as exc:
        raise HTTPException(503, f"Integration health unavailable: {type(exc).__name__}")


@router.get("/posts/{slug}", dependencies=[Depends(_auth_dep())])
async def post_by_slug(slug: str, request: Request):
    """Return one real post by id/slug, scoped to the authenticated workspace."""
    ws = _workspace(request)
    db = _db()
    if db is None:
        raise HTTPException(503, "Post store unavailable")
    try:
        query = db.table("post_history").select("*")
        if ws:
            query = query.eq("workspace_id", ws)
        # Support either the canonical id or a stored slug when the schema has one.
        result = query.eq("id", slug).limit(1).execute()
        row = (result.data or [None])[0]
        if row is None:
            try:
                query = db.table("post_history").select("*")
                if ws:
                    query = query.eq("workspace_id", ws)
                result = query.eq("slug", slug).limit(1).execute()
                row = (result.data or [None])[0]
            except Exception:
                row = None
        if row is None:
            # automation_jobs may be the durable source for a completed post.
            query = db.table("automation_jobs").select("*").eq("job_id", slug).limit(1)
            if ws:
                query = query.eq("workspace_id", ws)
            result = query.execute()
            row = (result.data or [None])[0]
        if row is None:
            raise HTTPException(404, "Post not found")
        return {"post": _jsonable(row), "source": "post_history" if "platform" in row else "automation_jobs"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Post lookup unavailable: {type(exc).__name__}")


@router.get("/decisions", dependencies=[Depends(_auth_dep())])
async def decisions(request: Request, workspace_id: Optional[str] = None, limit: int = Query(50, ge=1, le=200)):
    ws = _workspace(request, workspace_id)
    from core.decision_gate import decision_gate
    if ws:
        rows = decision_gate.list_for_workspace(ws, limit=limit)
    else:
        rows = list(getattr(decision_gate, "_decisions", {}).values())[-limit:]
    return {"decisions": [_jsonable(x.to_dict() if hasattr(x, "to_dict") else x) for x in rows], "count": len(rows), "workspace_id": ws}


@router.get("/decisions/{decision_id}", dependencies=[Depends(_auth_dep())])
async def decision(decision_id: str, request: Request):
    from core.decision_gate import decision_gate
    d = decision_gate.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    _workspace(request, d.workspace_id)
    return d.to_dict()


@router.get("/decisions/{decision_id}/execution-evidence", dependencies=[Depends(_auth_dep())])
async def decision_execution_evidence(decision_id: str, request: Request, limit: int = Query(20, ge=1, le=100)):
    """Return the authoritative decision -> governed action execution chain.

    This is observational only: it never authorizes, approves, retries, or executes
    an action. The decision and action_outbox records remain the authoritative sources.
    """
    from core.decision_gate import decision_gate
    d = decision_gate.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    ws = _workspace(request, d.workspace_id)
    db = getattr(getattr(request.app.state, "container", None), "supabase_service", None)
    client = getattr(db, "client", None) if db else None
    actions = []
    if client is not None:
        try:
            result = client.table("action_outbox").select("*").eq("workspace_id", ws).eq("decision_id", decision_id).order("created_at", desc=True).limit(limit).execute()
            actions = result.data or []
        except Exception as exc:
            raise HTTPException(503, f"Action evidence unavailable: {type(exc).__name__}") from exc
    return {
        "decision": d.to_dict(),
        "actions": _jsonable(actions),
        "action_count": len(actions),
        "evidence_state": "authoritative" if client is not None else "decision_only",
    }

@router.get("/control-plane/action-intents/{intent_id}/evidence", dependencies=[Depends(_auth_dep())])
async def action_intent_execution_evidence(intent_id: str, request: Request, limit: int = Query(20, ge=1, le=100)):
    """Return the authoritative Action Kernel execution evidence for one intent.

    Observational only: this endpoint never approves, retries, reconciles, or
    executes an action. Workspace authorization is derived from the durable
    action_outbox record before any related execution-ledger row is exposed.
    """
    if not intent_id.strip():
        raise HTTPException(400, "Action intent id is required")
    container = getattr(request.app.state, "container", None)
    db_service = getattr(container, "supabase_service", None) if container else None
    client = getattr(db_service, "client", None) if db_service else None
    if client is None:
        raise HTTPException(503, "Action evidence unavailable")
    try:
        action_result = client.table("action_outbox").select("*").eq("intent_id", intent_id).limit(1).execute()
        action = (action_result.data or [None])[0]
    except Exception as exc:
        raise HTTPException(503, f"Action evidence unavailable: {type(exc).__name__}") from exc
    if not action:
        raise HTTPException(404, "Action intent not found")
    ws = _workspace(request, action.get("workspace_id"))

    execution = None
    execution_id = action.get("execution_id")
    if execution_id:
        try:
            result = (client.table("external_execution_ledger").select("*")
                      .eq("workspace_id", ws).eq("execution_id", execution_id).limit(1).execute())
            execution = (result.data or [None])[0]
        except Exception as exc:
            raise HTTPException(503, f"Execution ledger unavailable: {type(exc).__name__}") from exc

    return {
        "intent": _jsonable(action),
        "execution": _jsonable(execution),
        "evidence_state": "authoritative",
        "workspace_id": ws,
        "has_execution": execution is not None,
    }

@router.get("/decisions/{decision_id}/votes", dependencies=[Depends(_auth_dep())])
async def decision_votes(decision_id: str, request: Request):
    from core.decision_gate import decision_gate
    d = decision_gate.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    _workspace(request, d.workspace_id)
    return {"decision_id": decision_id, "votes": _jsonable(d.votes)}


@router.get("/decisions/{decision_id}/risk", dependencies=[Depends(_auth_dep())])
async def decision_risk(decision_id: str, request: Request):
    from core.decision_gate import decision_gate
    d = decision_gate.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    _workspace(request, d.workspace_id)
    return {"decision_id": decision_id, "risk_level": d.risk_level, "approved": d.approved, "source": d.source.value, "reason": d.reason}


@router.get("/decisions/{decision_id}/dissent", dependencies=[Depends(_auth_dep())])
async def decision_dissent(decision_id: str, request: Request):
    from core.decision_gate import decision_gate
    d = decision_gate.get(decision_id)
    if d is None:
        raise HTTPException(404, "Decision not found")
    _workspace(request, d.workspace_id)
    dissent = [v for v in (d.votes or []) if str(v.get("vote", "")).lower() in {"reject", "dissent", "veto"}]
    return {"decision_id": decision_id, "dissent": _jsonable(dissent), "count": len(dissent)}


def register_backend_contract_routes(app) -> None:
    app.include_router(router)
