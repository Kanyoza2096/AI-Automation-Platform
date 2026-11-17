"""Read-only bridge from Meta Engine supervisor observations to Director evidence.

This projection preserves the supervisor's reported contract and makes its
limits explicit. It does not promote a supervisor report into independent
verification of the underlying service.
"""
from __future__ import annotations

from typing import Any

CONTRACT_NAME = "meta_engine.observations/v1"
VALID_CONTRACT_STATES = {"complete", "partial", "invalid_source", "unavailable"}


def project_meta_health_evidence(health: Any) -> dict[str, Any]:
    """Normalize Meta health evidence without synthesizing missing observations."""
    if not isinstance(health, dict):
        health = {}

    contract = health.get("engine_observation_contract")
    contract = contract if isinstance(contract, dict) else {}
    contract_name = contract.get("contract")
    contract_status = str(contract.get("status") or "unavailable").lower()
    raw_observations = health.get("engine_observations")
    raw_observations = raw_observations if isinstance(raw_observations, list) else []

    observations = []
    for item in raw_observations[:500]:
        if not isinstance(item, dict):
            continue
        service_id = item.get("service_id")
        status = item.get("status")
        observed_at = item.get("observed_at")
        if not isinstance(service_id, str) or not service_id.strip():
            continue
        if not isinstance(status, str) or not status.strip():
            continue
        if not isinstance(observed_at, str) or not observed_at.strip():
            continue
        observations.append({
            "service_id": service_id.strip(),
            "status": status.strip().lower(),
            "observed_at": observed_at,
            "age_seconds": item.get("age_seconds"),
            "source_version": item.get("source_version"),
            "reported_by": "meta_engine",
            "evidence_class": "supervisor_observation",
            "independently_verified": False,
        })

    accepted_count = contract.get("accepted_count")
    rejected_count = contract.get("rejected_count")
    source_count = contract.get("source_count")
    count_integrity = (
        isinstance(accepted_count, int)
        and not isinstance(accepted_count, bool)
        and accepted_count == len(observations)
    )
    contract_valid = contract_name == CONTRACT_NAME and contract_status in VALID_CONTRACT_STATES
    if not contract_valid:
        integrity_status = "contract_unavailable_or_unrecognised"
    elif not count_integrity:
        integrity_status = "accepted_count_mismatch"
    else:
        integrity_status = "consistent"

    reported_status = str(health.get("status") or "unknown").lower()
    available = health.get("available") is True
    if not available or not contract_valid:
        evidence_status = "unavailable"
    elif integrity_status != "consistent":
        evidence_status = "inconsistent"
    else:
        evidence_status = contract_status

    return {
        "contract": CONTRACT_NAME,
        "status": evidence_status,
        "integrity_status": integrity_status,
        "source": "meta_engine.check_health",
        "reported_system_status": reported_status,
        "health_score": health.get("health_score"),
        "risk_score": health.get("risk_score"),
        "source_count": source_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "freshness_seconds": contract.get("freshness_seconds"),
        "generated_at": contract.get("generated_at"),
        "observations": observations,
        "rejected": [
            item for item in (contract.get("rejected") or [])[:500]
            if isinstance(item, dict)
        ],
        "policy": {
            "authority": "supervisor_reported",
            "independent_verification": False,
            "unknown_is_not_healthy": True,
            "missing_observations_are_not_synthesized": True,
            "grants_execution_authority": False,
            "interpretation": (
                "Meta Engine reports these observations; this contract does not "
                "independently verify each underlying engine or its business effects."
            ),
        },
    }


def read_meta_health_evidence() -> dict[str, Any]:
    """Read the in-process Meta Engine health contract, failing closed."""
    try:
        from services.meta_engine import meta_engine
        checker = getattr(meta_engine, "check_health", None)
        if not callable(checker):
            return project_meta_health_evidence(None)
        return project_meta_health_evidence(checker())
    except Exception:
        return project_meta_health_evidence(None)
