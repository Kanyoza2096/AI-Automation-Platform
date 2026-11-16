"""Meta-engine HTTP surface — Control Plane wire-up (best-effort)."""
from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger("kanyoza.routes.meta")
from core.security.deps import require_platform_or_species_privileges

router = APIRouter(prefix="/api/v1/meta", tags=["meta"], dependencies=[Depends(require_platform_or_species_privileges)])

_incidents: list = []
_snapshots: list = []
_healing_history: list = []


def _engines():
    return [
        {"id": "guardian", "status": "unknown"},
        {"id": "council", "status": "unknown"},
        {"id": "director", "status": "unknown"},
        {"id": "metabolism", "status": "unknown"},
        {"id": "circulation", "status": "unknown"},
        {"id": "pulse", "status": "unknown"},
    ]


@router.get("/status")
async def meta_status():
    """Return authoritative Meta Engine health and its evidence-quality contract."""
    try:
        from services.meta_engine import meta_engine
        checker = getattr(meta_engine, "check_health", None)
        if not callable(checker):
            raise RuntimeError("meta_engine_health_provider_unavailable")
        health = checker()
        if not isinstance(health, dict):
            raise RuntimeError("meta_engine_health_payload_invalid")
        engine_states = health.get("engine_states")
        if not isinstance(engine_states, dict):
            engine_states = {}
        engines = [
            {"id": str(engine_id), "status": str(engine_status or "unknown").lower()}
            for engine_id, engine_status in sorted(engine_states.items())
        ]
        return {
            "ok": bool(health.get("available", False)),
            "status": str(health.get("status", "unknown")).lower(),
            "health_score": health.get("health_score"),
            "risk_score": health.get("risk_score"),
            "engines": engines,
            "engine_observations": health.get("engine_observations", []),
            "engine_observation_contract": health.get("engine_observation_contract", {
                "contract": "meta_engine.observations/v1",
                "status": "unavailable",
                "reason": "observation_contract_missing",
            }),
            "unknown_engines": health.get("unknown_engines", []),
            "degraded_engines": health.get("degraded_engines", []),
            "offline_engines": health.get("offline_engines", []),
            "source": "meta_engine.check_health",
        }
    except Exception as exc:
        logger.warning("Meta status unavailable: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "engines": [],
            "engine_observations": [],
            "engine_observation_contract": {
                "contract": "meta_engine.observations/v1",
                "status": "unavailable",
                "reason": "authoritative_health_provider_unavailable",
            },
            "message": "No authoritative Meta Engine health evidence is available.",
            "source": "none",
        }


@router.get("/engines")
async def meta_engines():
    try:
        from services.meta_engine import meta_engine
        status = meta_engine.get_status() if hasattr(meta_engine, "get_status") else None
        raw_engines = getattr(meta_engine, "_engines", {}) or {}
        engines = []
        if isinstance(raw_engines, dict) and raw_engines:
            for eid, eng in raw_engines.items():
                st = getattr(eng, "status", None)
                if hasattr(st, "value"):
                    st = st.value
                engines.append({"id": str(eid), "status": str(st or "unknown").lower()})
            return {"ok": True, "engines": engines, "source": "meta_engine"}
        if isinstance(status, dict) and status.get("engine_count"):
            return {"ok": True, "engines": _engines(), "engine_count": status.get("engine_count"), "source": "meta_engine"}
    except Exception as exc:
        logger.warning("Meta engines unavailable: %s", type(exc).__name__)
    return {"ok": True, "engines": _engines(), "source": "fallback"}


@router.get("/diagnoses")
async def meta_diagnoses():
    try:
        from services.meta_engine import meta_engine
        rows = []
        for d in list(getattr(meta_engine, "_diagnoses", []) or [])[-50:]:
            rows.append({
                "id": getattr(d, "id", None),
                "engine": getattr(d, "engine", None),
                "symptom": getattr(d, "symptom", None),
                "root_cause": getattr(d, "root_cause", None),
                "severity": getattr(getattr(d, "severity", None), "value", getattr(d, "severity", None)),
                "recommended_action": getattr(d, "recommended_action", None),
                "confidence": getattr(d, "confidence", None),
            })
        if not rows and hasattr(meta_engine, "get_alerts"):
            rows = meta_engine.get_alerts() or []
        return {"ok": True, "diagnoses": rows, "count": len(rows), "source": "meta_engine"}
    except Exception as exc:
        logger.warning("meta_diagnoses failed: %s", type(exc).__name__)
        return {"ok": True, "diagnoses": [], "count": 0, "source": "none"}


@router.get("/dependency-graph")
async def meta_dependency_graph():
    return {
        "ok": True,
        "nodes": [e["id"] for e in _engines()],
        "edges": [
            {"from": "guardian", "to": "director"},
            {"from": "council", "to": "director"},
            {"from": "metabolism", "to": "director"},
            {"from": "director", "to": "circulation"},
        ],
    }


# ✅ ADDED: Snapshots endpoint
@router.get("/snapshots")
async def meta_snapshots():
    """Return Meta Engine system snapshots (bounded history)."""
    try:
        from services.meta_engine import meta_engine
        snapshots = getattr(meta_engine, "_snapshots", None)
        if snapshots is not None:
            return {
                "ok": True,
                "snapshots": [
                    {
                        "timestamp": iso(s.timestamp),
                        "overall_status": s.overall_status.value,
                        "health_score": s.health_score,
                        "risk_score": s.risk_score,
                        "engine_count": len(s.engines),
                    }
                    for s in list(snapshots)[-12:]
                ],
            }
    except Exception as exc:
        logger.warning("Meta snapshots failed: %s", exc)
    return {"ok": True, "snapshots": list(_snapshots)}


# ✅ ADDED: Healing history endpoint
@router.get("/healing-history")
async def meta_healing_history():
    """Return Meta Engine recovery/healing history (bounded)."""
    try:
        from services.meta_engine import meta_engine
        history = getattr(meta_engine, "_recovery_history", None)
        if history is not None:
            return {
                "ok": True,
                "healing_history": [
                    {
                        "id": a.id,
                        "engine": a.engine,
                        "reason": a.reason,
                        "status": a.status.value,
                        "started_at": iso(a.started_at),
                        "finished_at": iso(a.finished_at),
                        "verification_passed": a.verification_passed,
                    }
                    for a in list(history)[-20:]
                ],
            }
    except Exception as exc:
        logger.warning("Meta healing history failed: %s", exc)
    return {"ok": True, "healing_history": list(_healing_history)}


def _jsonable_diagnosis(d) -> dict:
    return {
        "id": getattr(d, "id", None),
        "engine": getattr(d, "engine", None),
        "symptom": getattr(d, "symptom", None),
        "root_cause": getattr(d, "root_cause", None),
        "severity": getattr(getattr(d, "severity", None), "value", getattr(d, "severity", None)),
        "recommended_action": getattr(d, "recommended_action", None),
        "auto_fixable": bool(getattr(d, "auto_fixable", False)),
        "confidence": getattr(d, "confidence", None),
        "risk_score": getattr(d, "risk_score", None),
        "affected_engines": list(getattr(d, "affected_engines", []) or []),
        "narrative": getattr(d, "narrative", None),
    }


@router.post("/diagnose")
async def meta_diagnose():
    try:
        from services.meta_engine import meta_engine
        # Prefer fresh snapshot path when async health is available
        if hasattr(meta_engine, "check_health_async"):
            try:
                await meta_engine.check_health_async()
            except Exception as exc:
                logger.warning("meta check_health_async during diagnose: %s", type(exc).__name__)
        diagnoses = meta_engine.diagnose() if hasattr(meta_engine, "diagnose") else []
        cascades = []
        if hasattr(meta_engine, "detect_cascading_failures"):
            try:
                cascades = meta_engine.detect_cascading_failures() or []
            except Exception as exc:
                logger.warning("cascade detect failed: %s", type(exc).__name__)
        payload = [_jsonable_diagnosis(d) for d in (diagnoses or [])]
        payload.extend(_jsonable_diagnosis(d) for d in (cascades or []))
        return {
            "ok": True,
            "status": "complete",
            "diagnoses": payload,
            "count": len(payload),
            "source": "meta_engine",
        }
    except Exception as exc:
        logger.warning("meta_diagnose failed: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "diagnoses": [],
            "message": f"Meta Engine diagnose failed: {type(exc).__name__}",
            "source": "none",
        }


@router.post("/heal")
async def meta_heal():
    try:
        from services.meta_engine import meta_engine
        diagnoses = meta_engine.diagnose() if hasattr(meta_engine, "diagnose") else []
        started = []
        skipped = []
        for d in diagnoses or []:
            entry = {
                "id": getattr(d, "id", None),
                "engine": getattr(d, "engine", None),
                "auto_fixable": bool(getattr(d, "auto_fixable", False)),
            }
            if not getattr(d, "auto_fixable", False):
                entry["reason"] = "not_auto_fixable"
                skipped.append(entry)
                continue
            if hasattr(meta_engine, "_start_recovery"):
                ok = meta_engine._start_recovery(d)
                entry["started"] = bool(ok)
                if ok:
                    started.append(entry)
                else:
                    entry["reason"] = "recovery_not_started"
                    skipped.append(entry)
            else:
                entry["reason"] = "no_recovery_api"
                skipped.append(entry)
        return {
            "ok": True,
            "status": "complete",
            "healed": started,
            "started": started,
            "skipped": skipped,
            "count_started": len(started),
            "source": "meta_engine",
        }
    except Exception as exc:
        logger.warning("meta_heal failed: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "healed": [],
            "message": f"Meta Engine heal failed: {type(exc).__name__}",
            "source": "none",
        }


@router.get("/incidents")
async def meta_incidents():
    try:
        from services.meta_engine import meta_engine
        if hasattr(meta_engine, "get_incidents"):
            rows = meta_engine.get_incidents() or []
            return {"ok": True, "incidents": rows, "count": len(rows), "source": "meta_engine"}
    except Exception as exc:
        logger.warning("meta_incidents failed: %s", type(exc).__name__)
    return {"ok": True, "incidents": list(_incidents), "count": len(_incidents), "source": "fallback"}


@router.post("/resolve/{incident_id}")
async def meta_resolve(incident_id: str):
    try:
        from services.meta_engine import meta_engine
        incidents = getattr(meta_engine, "_incidents", None)
        if isinstance(incidents, dict) and incident_id in incidents:
            incident = incidents[incident_id]
            # Prefer enum if present
            try:
                from services.meta_engine import IncidentStatus
                incident.status = IncidentStatus.RESOLVED
            except Exception:
                setattr(incident, "status", "resolved")
            from datetime import datetime, timezone
            if hasattr(incident, "resolved_at"):
                incident.resolved_at = datetime.now(timezone.utc)
            return {
                "ok": True,
                "resolved": True,
                "incident_id": incident_id,
                "source": "meta_engine",
            }
        # Soft resolve: accept id even if not in map (operator ack)
        return {
            "ok": True,
            "resolved": False,
            "incident_id": incident_id,
            "message": "Incident id not found in Meta Engine incident map",
            "source": "meta_engine",
        }
    except Exception as exc:
        logger.warning("meta_resolve failed: %s", type(exc).__name__)
        return {
            "ok": False,
            "status": "unavailable",
            "incident_id": incident_id,
            "resolved": False,
            "message": f"Resolve failed: {type(exc).__name__}",
            "source": "none",
        }


def iso(value):
    return value.isoformat() if value else None


def register_meta_routes(app) -> None:
    app.include_router(router)
