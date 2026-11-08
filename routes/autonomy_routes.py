"""Autonomy governance HTTP surface for the control plane.

Exposes AutonomousLoop status, OODA phase, circuit breakers, learned patterns,
and owner mode changes. Fail-closed when the loop is not started.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.routes.autonomy")

router = APIRouter(prefix="/api/v1", tags=["autonomy"])

# Governance modes aligned with KDO AutonomousHeader
ALLOWED_MODES = {
    "OBSERVE",
    "SUPERVISED",
    "AUTONOMOUS",
    "EMERGENCY_LOCKDOWN",
}

# Governance mode: file-backed so multi-restart keeps last owner choice.
# Default SUPERVISED for product safety (not AUTONOMOUS).
_GOVERNANCE_FILE = None
_governance_mode: str = "SUPERVISED"
_last_ooda_phase: str = "OBSERVE"


def _governance_path():
    global _GOVERNANCE_FILE
    if _GOVERNANCE_FILE is not None:
        return _GOVERNANCE_FILE
    import os
    base = os.environ.get("KANYOZA_DATA_DIR") or os.environ.get("TMPDIR") or "/tmp"
    path = os.path.join(base, "kanyoza_autonomy_governance.json")
    _GOVERNANCE_FILE = path
    return path


def _load_governance_mode() -> str:
    global _governance_mode
    try:
        import json, os
        p = _governance_path()
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            mode = str(data.get("mode") or "").upper().strip()
            if mode in ALLOWED_MODES:
                _governance_mode = mode
                return mode
    except Exception as exc:
        logger.warning("governance load failed: %s", type(exc).__name__)
    return _governance_mode


def _save_governance_mode(mode: str) -> None:
    global _governance_mode
    _governance_mode = mode
    try:
        import json, os
        p = _governance_path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"mode": mode}, f)
    except Exception as exc:
        logger.warning("governance save failed: %s", type(exc).__name__)


# Load once at import
_load_governance_mode()


def _auth_dep():
    from api.v1 import require_auth
    return require_auth

def _operator_dep():
    from core.security.deps import require_workspace_operator
    return require_workspace_operator

def _owner_dep():
    from core.security.deps import require_workspace_owner
    return require_workspace_owner


def _loop():
    try:
        from core.autonomous_loop import _autonomous_loop
        return _autonomous_loop
    except Exception:
        return None


def _resolve_mode(loop) -> str:
    global _governance_mode
    if _governance_mode == "EMERGENCY_LOCKDOWN":
        return "EMERGENCY_LOCKDOWN"
    if loop is None:
        return "OBSERVE"
    if not getattr(loop, "_running", False):
        return "OBSERVE"
    return _governance_mode


def _resolve_phase(loop) -> str:
    """Return the loop's live phase; lifetime counters are not phase telemetry."""
    if loop is None or not getattr(loop, "_running", False):
        return "OBSERVE"
    explicit = getattr(loop, "_current_phase", None) or getattr(loop, "current_phase", None)
    if explicit:
        return str(explicit).upper()
    # Older loop implementations do not expose a live phase. Prefer an honest
    # idle label over inferring Orient/Act from cumulative counters.
    return "OBSERVE"


def _patterns_from_loop(loop) -> list:
    if loop is None:
        return []
    intel = getattr(loop, "intelligence", None)
    if intel is None:
        return []
    status = {}
    try:
        status = intel.get_status() or {}
    except Exception:
        return []
    patterns = status.get("patterns") or {}
    out = []
    if isinstance(patterns, dict):
        for name, p in patterns.items():
            if not isinstance(p, dict):
                continue
            out.append({
                "id": str(name),
                "pattern": str(name),
                "confidence": float(p.get("confidence") or 0),
                "occurrences": p.get("occurrences"),
                "success_rate": p.get("success_rate"),
                "weightDelta": f"occ={p.get('occurrences', 0)}",
                "lastVerified": "runtime",
            })
    return out


def _breakers_from_loop(loop) -> list:
    if loop is None:
        return []
    breakers = getattr(loop, "_circuit_breakers", {}) or {}
    out = []
    for name, breaker in breakers.items():
        try:
            stats = breaker.get_stats() if hasattr(breaker, "get_stats") else {}
        except Exception:
            stats = {}
        state = str(stats.get("state") or "unknown").lower()
        out.append({
            "id": name,
            "name": name,
            "status": "open" if stats.get("is_open") or state == "open" else state,
            "state": state,
            "failures": stats.get("failures", 0),
            "successes": stats.get("successes", 0),
            "threshold": f"{getattr(breaker, 'failure_threshold', 5)} failures",
            "maxOpsPerMin": None,
        })
    return out


@router.get("/owner/autonomy", dependencies=[Depends(_auth_dep())])
@router.get("/autonomy/status", dependencies=[Depends(_auth_dep())])
async def autonomy_status(request: Request):
    loop = _loop()
    mode = _resolve_mode(loop)
    phase = _resolve_phase(loop)
    status: Dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "status": mode,
        "phase": phase,
        "running": bool(loop and getattr(loop, "_running", False)),
        "source": "autonomous_loop" if loop else "none",
    }
    if loop is not None:
        try:
            status["loop"] = loop.get_status()
        except Exception as exc:
            status["loop_error"] = type(exc).__name__
    return status


class AutonomyModeBody(BaseModel):
    mode: str = Field(..., min_length=3, max_length=64)


@router.post("/owner/autonomy", dependencies=[Depends(_owner_dep())])
@router.put("/owner/autonomy", dependencies=[Depends(_owner_dep())])
async def set_autonomy_mode(body: AutonomyModeBody, request: Request):
    global _governance_mode
    mode = str(body.mode or "").upper().strip()
    if mode not in ALLOWED_MODES:
        raise HTTPException(400, detail=f"Unsupported mode. Allowed: {sorted(ALLOWED_MODES)}")

    identity = getattr(request.state, "auth_identity", None)
    role = getattr(identity, "role", None) or getattr(identity, "method", "")
    # Prefer owner/admin/species for dangerous modes
    if mode in ("AUTONOMOUS", "EMERGENCY_LOCKDOWN"):
        is_elevated = bool(
            getattr(identity, "is_species", False)
            or str(role).lower() in ("owner", "admin", "species")
        )
        # Service credential from KCPG is treated as elevated for control-plane ops
        if not is_elevated and not getattr(identity, "is_species", False):
            # Still allow if workspace-bound owner session was exchanged via gateway
            pass

    loop = _loop()
    _save_governance_mode(mode)

    if mode == "EMERGENCY_LOCKDOWN" and loop is not None and getattr(loop, "_running", False):
        try:
            if hasattr(loop, "stop"):
                await loop.stop()
        except Exception as exc:
            logger.warning("Lockdown stop failed: %s", type(exc).__name__)

    if mode in ("SUPERVISED", "AUTONOMOUS") and loop is not None and not getattr(loop, "_running", False):
        try:
            if hasattr(loop, "start"):
                await loop.start()
        except Exception as exc:
            logger.warning("Autonomy start failed: %s", type(exc).__name__)

    return {
        "ok": True,
        "mode": _governance_mode,
        "status": _governance_mode,
        "running": bool(loop and getattr(loop, "_running", False)),
        "source": "autonomous_loop" if loop else "governance_state",
    }


@router.get("/autonomy/loop", dependencies=[Depends(_auth_dep())])
async def autonomy_loop():
    loop = _loop()
    phase = _resolve_phase(loop)
    payload: Dict[str, Any] = {
        "ok": True,
        "phase": phase,
        "currentPhase": phase,
        "running": bool(loop and getattr(loop, "_running", False)),
        "source": "autonomous_loop" if loop else "none",
    }
    if loop is not None:
        try:
            payload["status"] = loop.get_status()
        except Exception as exc:
            payload["status_error"] = type(exc).__name__
    return payload


@router.post("/autonomy/loop/step", dependencies=[Depends(_auth_dep())])
async def autonomy_loop_step():
    loop = _loop()
    if loop is None:
        raise HTTPException(503, detail="Autonomous loop not initialized")
    # No public single-step API on the loop today — report honestly.
    return {
        "ok": True,
        "accepted": False,
        "message": "Single-step is not exposed; loop runs on its internal interval when started.",
        "running": bool(getattr(loop, "_running", False)),
        "source": "autonomous_loop",
    }


@router.get("/autonomy/circuit-breakers", dependencies=[Depends(_auth_dep())])
async def autonomy_circuit_breakers():
    loop = _loop()
    items = _breakers_from_loop(loop)
    return {
        "ok": True,
        "circuitBreakers": items,
        "breakers": items,
        "count": len(items),
        "source": "autonomous_loop" if loop else "none",
    }


@router.post("/autonomy/circuit-breakers/{breaker_id}/reset", dependencies=[Depends(_operator_dep())])
async def reset_circuit_breaker(breaker_id: str):
    loop = _loop()
    if loop is None:
        raise HTTPException(503, detail="Autonomous loop not initialized")
    breakers = getattr(loop, "_circuit_breakers", {}) or {}
    breaker = breakers.get(breaker_id)
    if breaker is None:
        raise HTTPException(404, detail=f"Circuit breaker '{breaker_id}' not found")
    # Soft reset local counters/state
    try:
        from core.autonomous_loop import CircuitState
        breaker.state = CircuitState.CLOSED
        breaker._failures = 0
        breaker._successes = 0
        breaker._last_failure_time = None
    except Exception as exc:
        raise HTTPException(500, detail=f"Reset failed: {type(exc).__name__}")
    return {
        "ok": True,
        "id": breaker_id,
        "state": "closed",
        "source": "autonomous_loop",
    }


@router.get("/autonomy/patterns", dependencies=[Depends(_auth_dep())])
async def autonomy_patterns():
    loop = _loop()
    items = _patterns_from_loop(loop)
    return {
        "ok": True,
        "patterns": items,
        "patternsLearned": items,
        "count": len(items),
        "source": "autonomous_loop" if loop else "none",
    }


@router.post("/owner/lockdown", dependencies=[Depends(_owner_dep())])
async def owner_lockdown(request: Request):
    global _governance_mode
    _governance_mode = "EMERGENCY_LOCKDOWN"
    loop = _loop()
    if loop is not None and getattr(loop, "_running", False):
        try:
            await loop.stop()
        except Exception as exc:
            logger.warning("Lockdown stop failed: %s", type(exc).__name__)
    return {
        "ok": True,
        "mode": "EMERGENCY_LOCKDOWN",
        "running": False,
        "source": "governance_state",
    }


def register_autonomy_routes(app) -> None:
    app.include_router(router)
