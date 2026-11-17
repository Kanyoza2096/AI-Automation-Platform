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
    valid_counts = all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (source_count, accepted_count, rejected_count)
    )
    count_integrity = (
        valid_counts
        and accepted_count == len(raw_observations) == len(observations)
        and (contract_status == "invalid_source" or source_count == accepted_count + rejected_count)
    )
    contract_valid = contract_name == CONTRACT_NAME and contract_status in VALID_CONTRACT_STATES
    if not contract_valid:
        integrity_status = "contract_unavailable_or_unrecognised"
    elif not count_integrity:
        integrity_status = "observation_count_mismatch"
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


def _status_class(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    if status in {"healthy", "nominal", "ok", "ready", "running"}:
        return "healthy"
    if status in {"degraded", "warning", "partial"}:
        return "degraded"
    if status in {"critical", "failed", "down"}:
        return "critical"
    if status in {"offline", "stopped"}:
        return "offline"
    return "unknown"


def reconcile_supervisor_evidence(
    decision_context: Any, supervisor_evidence: Any
) -> dict[str, Any]:
    """Compare Meta reports with fresh Director facts using exact source identity only."""
    context = dict(decision_context) if isinstance(decision_context, dict) else {}
    evidence = supervisor_evidence if isinstance(supervisor_evidence, dict) else {}
    facts = list(context.get("facts") or [])
    unknowns = list(context.get("unknowns") or [])
    conflicts = list(context.get("conflicts") or [])

    comparisons = []
    for item in evidence.get("observations", [])[:500]:
        if not isinstance(item, dict):
            continue
        service_id_display = str(item.get("service_id") or "").strip()
        service_id = service_id_display.casefold()
        if not service_id:
            continue
        facts.append({
            "kind": "supervisor_reported_engine_status",
            "observer": "meta_engine",
            "source": "meta_engine.check_health",
            "subject_service_id": service_id_display,
            "status": item.get("status", "unknown"),
            "observed_at": item.get("observed_at"),
            "evidence_class": "supervisor_observation",
            "independently_verified": False,
        })
        matches = [
            fact for fact in facts
            if isinstance(fact, dict)
            and service_id in {
                str(fact.get("observer") or "").strip().casefold(),
                str(fact.get("source") or "").strip().casefold(),
            }
        ]
        comparison = {
            "service_id": item["service_id"],
            "supervisor_status": item.get("status", "unknown"),
            "director_status": None,
            "result": "unmatched",
            "identity_match": "exact",
            "independent_verification": False,
        }
        if len(matches) == 1:
            director_status = matches[0].get("status", "unknown")
            comparison["director_status"] = director_status
            supervisor_class = _status_class(item.get("status"))
            director_class = _status_class(director_status)
            if "unknown" in {supervisor_class, director_class}:
                comparison["result"] = "unknown"
                unknowns.append({
                    "kind": "supervisor_director_status_comparison_unknown",
                    "observer": item["service_id"],
                    "supervisor_status": item.get("status"),
                    "director_status": director_status,
                })
            elif supervisor_class != director_class:
                comparison["result"] = "disagreement"
                conflicts.append({
                    "kind": "supervisor_director_status_disagreement",
                    "observer": item["service_id"],
                    "supervisor_status": item.get("status"),
                    "director_status": director_status,
                    "interpretation": (
                        "Meta Engine and Director report different statuses for the same "
                        "exact observer identity. Neither report is silently preferred."
                    ),
                })
            else:
                comparison["result"] = "agreement"
        elif len(matches) > 1:
            comparison["result"] = "ambiguous"
            unknowns.append({
                "kind": "supervisor_observer_identity_ambiguous",
                "observer": item["service_id"],
                "matching_director_facts": len(matches),
            })
        else:
            unknowns.append({
                "kind": "supervisor_observation_not_matched",
                "observer": item["service_id"],
                "interpretation": (
                    "No fresh Director fact with the exact same observer or source "
                    "identity was available for comparison."
                ),
            })
        comparisons.append(comparison)

    status = str(evidence.get("status") or "unavailable").lower()
    if status not in {"complete", "partial"}:
        unknowns.append({
            "kind": "supervisor_observation_contract_not_usable",
            "contract_status": status,
            "integrity_status": evidence.get("integrity_status", "unknown"),
        })

    counts = {
        "compared": len(comparisons),
        "agreements": sum(item["result"] == "agreement" for item in comparisons),
        "disagreements": sum(item["result"] == "disagreement" for item in comparisons),
        "unmatched": sum(item["result"] == "unmatched" for item in comparisons),
        "ambiguous": sum(item["result"] == "ambiguous" for item in comparisons),
        "unknown": sum(item["result"] == "unknown" for item in comparisons),
    }
    reconciliation_status = (
        "disagreement" if counts["disagreements"] else
        "partial" if counts["unmatched"] or counts["ambiguous"] or counts["unknown"] or status != "complete" else
        "agreement" if counts["compared"] else "unavailable"
    )
    context["facts"] = facts
    context["unknowns"] = unknowns
    context["conflicts"] = conflicts
    context["supervisor_observation_evidence"] = evidence
    context["supervisor_evidence_reconciliation"] = {
        "status": reconciliation_status,
        "counts": counts,
        "comparisons": comparisons,
        "independence_established": False,
        "authority": "read_only_advisory",
        "grants_execution_authority": False,
    }
    return context


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
