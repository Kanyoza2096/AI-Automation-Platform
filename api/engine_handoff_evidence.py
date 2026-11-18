"""Read-only, platform-scoped evidence adapters for OODA and Meta collaboration.

These adapters expose bounded observations only. They never start/step the OODA
loop, change autonomy mode, or invoke Meta recovery/healing.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any


def _bounded(value: Any, limit: int = 300) -> str:
    return str(value or "")[:limit]


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _diagnosis_record(item: Any) -> dict[str, Any]:
    severity = getattr(item, "severity", None)
    severity = getattr(severity, "value", severity)
    return {
        "id": _bounded(getattr(item, "id", None), 100),
        "engine": _bounded(getattr(item, "engine", None), 100),
        "symptom": _bounded(getattr(item, "symptom", None), 500),
        "root_cause": _bounded(getattr(item, "root_cause", None), 500),
        "severity": _bounded(severity, 40),
        "recommended_action": _bounded(getattr(item, "recommended_action", None), 500),
        "confidence": getattr(item, "confidence", None),
        "independently_verified": False,
    }


async def load_ooda_evidence() -> dict[str, Any]:
    """Read the live loop's status without advancing or mutating its lifecycle."""
    try:
        from core.autonomous_loop import _autonomous_loop

        loop = _autonomous_loop
        if loop is None:
            raise RuntimeError("autonomous_loop_unavailable")
        getter = getattr(loop, "get_status", None)
        status = await _resolve(getter()) if callable(getter) else {}
        if not isinstance(status, dict):
            status = {}
        phase = getattr(loop, "_current_phase", None) or getattr(loop, "current_phase", None) or "OBSERVE"
        return {
            "status": "available",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "running": bool(getattr(loop, "_running", False)),
            "phase": _bounded(phase, 40).upper(),
            "loop_status": status,
            "objective": "Operator objective supplied separately to the consultation; no objective was submitted to or executed by the loop.",
            "authority": "read_only_observation",
            "execution_authority": "none",
            "limitations": [
                "This is a status observation, not an OODA decision or ACT-stage execution.",
                "The loop may change after this observation.",
            ],
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "reason": type(exc).__name__,
            "authority": "read_only_observation",
            "execution_authority": "none",
        }


async def load_meta_evidence() -> dict[str, Any]:
    """Request a fresh Meta health/diagnostic snapshot; never start recovery."""
    try:
        from services.meta_engine import meta_engine

        refresh = getattr(meta_engine, "check_health_async", None)
        if callable(refresh):
            await _resolve(refresh())
        checker = getattr(meta_engine, "check_health", None)
        if not callable(checker):
            raise RuntimeError("meta_health_provider_unavailable")
        health = await _resolve(checker())
        if not isinstance(health, dict):
            raise RuntimeError("meta_health_payload_invalid")

        diagnose = getattr(meta_engine, "diagnose", None)
        diagnoses = await _resolve(diagnose()) if callable(diagnose) else []
        diagnoses = diagnoses if isinstance(diagnoses, (list, tuple)) else []
        observations = health.get("engine_observations")
        observations = observations if isinstance(observations, list) else []
        contract = health.get("engine_observation_contract")
        contract = contract if isinstance(contract, dict) else {"status": "unavailable"}

        return {
            "status": "available" if health.get("available") is True else "partial",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "reported_status": _bounded(health.get("status"), 40).lower() or "unknown",
            "health_score": health.get("health_score"),
            "risk_score": health.get("risk_score"),
            "observation_contract": {
                "contract": _bounded(contract.get("contract"), 80),
                "status": _bounded(contract.get("status"), 40).lower() or "unavailable",
                "source_count": contract.get("source_count"),
                "accepted_count": contract.get("accepted_count"),
                "rejected_count": contract.get("rejected_count"),
                "freshness_seconds": contract.get("freshness_seconds"),
            },
            "engine_observations": [
                {
                    "service_id": _bounded(item.get("service_id"), 100),
                    "status": _bounded(item.get("status"), 40).lower() or "unknown",
                    "observed_at": _bounded(item.get("observed_at"), 80),
                    "age_seconds": item.get("age_seconds"),
                    "independently_verified": False,
                }
                for item in observations[:100] if isinstance(item, dict)
            ],
            "diagnoses": [_diagnosis_record(item) for item in diagnoses[:50]],
            "authority": "supervisor_reported",
            "execution_authority": "none",
            "recovery_started": False,
            "limitations": [
                "Meta Engine observations and diagnoses are supervisor-reported, not independent verification.",
                "No recovery, healing, remediation, or control change was requested or performed.",
            ],
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "reason": type(exc).__name__,
            "authority": "supervisor_reported",
            "execution_authority": "none",
            "recovery_started": False,
        }


async def load_engine_handoff_evidence(engine: str) -> dict[str, Any]:
    engine = str(engine or "").strip().lower()
    if engine == "ooda":
        return await load_ooda_evidence()
    if engine == "meta":
        return await load_meta_evidence()
    raise ValueError("Unsupported platform engine handoff")
