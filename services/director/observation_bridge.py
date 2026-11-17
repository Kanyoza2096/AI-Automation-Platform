"""Read-only bridge from Meta Engine supervisor observations to Director evidence.

The bridge uses a declared canonical identity map and reports observer coverage
separately from health. It never treats a missing observer as healthy.
"""
from __future__ import annotations

from typing import Any

CONTRACT_NAME = "meta_engine.observations/v1"
VALID_CONTRACT_STATES = {"complete", "partial", "invalid_source", "unavailable"}

# Explicit, reviewed identity equivalence only. Do not add aliases merely because
# two names look related; they must identify the same runtime service.
CANONICAL_SERVICE_ALIASES = {
    "meta_director": "director",
}


def canonical_service_id(value: Any) -> str:
    service_id = str(value or "").strip().casefold()
    return CANONICAL_SERVICE_ALIASES.get(service_id, service_id)


def _declared_director_observers() -> set[str]:
    """Read the authoritative Director observer registry without importing engines."""
    try:
        from .analysis import ENGINE_REGISTRY
        return {
            canonical_service_id(entry[2])
            for entry in ENGINE_REGISTRY
            if isinstance(entry, (tuple, list)) and len(entry) >= 3 and entry[2]
        }
    except Exception:
        return set()


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
        service_id, status, observed_at = item.get("service_id"), item.get("status"), item.get("observed_at")
        if not isinstance(service_id, str) or not service_id.strip():
            continue
        if not isinstance(status, str) or not status.strip():
            continue
        if not isinstance(observed_at, str) or not observed_at.strip():
            continue
        observations.append({
            "service_id": service_id.strip(),
            "canonical_service_id": canonical_service_id(service_id),
            "status": status.strip().lower(),
            "observed_at": observed_at,
            "age_seconds": item.get("age_seconds"),
            "source_version": item.get("source_version"),
            "reported_by": "meta_engine",
            "evidence_class": "supervisor_observation",
            "independently_verified": False,
        })

    accepted_count, rejected_count, source_count = (
        contract.get("accepted_count"), contract.get("rejected_count"), contract.get("source_count")
    )
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
    integrity_status = (
        "contract_unavailable_or_unrecognised" if not contract_valid else
        "observation_count_mismatch" if not count_integrity else "consistent"
    )
    available = health.get("available") is True
    evidence_status = (
        "unavailable" if not available or not contract_valid else
        "inconsistent" if integrity_status != "consistent" else contract_status
    )
    return {
        "contract": CONTRACT_NAME,
        "status": evidence_status,
        "integrity_status": integrity_status,
        "source": "meta_engine.check_health",
        "reported_system_status": str(health.get("status") or "unknown").lower(),
        "health_score": health.get("health_score"),
        "risk_score": health.get("risk_score"),
        "source_count": source_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "freshness_seconds": contract.get("freshness_seconds"),
        "generated_at": contract.get("generated_at"),
        "observations": observations,
        "rejected": [item for item in (contract.get("rejected") or [])[:500] if isinstance(item, dict)],
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


def _identity_values(record: dict) -> set[str]:
    # "source" is provenance, not service identity, and is intentionally excluded.
    return {
        canonical_service_id(record.get(key))
        for key in ("service_id", "subject_service_id", "observer")
        if canonical_service_id(record.get(key))
    }


def reconcile_supervisor_evidence(decision_context: Any, supervisor_evidence: Any) -> dict[str, Any]:
    """Compare reports by canonical exact identity and expose observer coverage."""
    context = dict(decision_context) if isinstance(decision_context, dict) else {}
    evidence = supervisor_evidence if isinstance(supervisor_evidence, dict) else {}
    facts = list(context.get("facts") or [])
    unknowns = list(context.get("unknowns") or [])
    conflicts = list(context.get("conflicts") or [])
    declared_observers = _declared_director_observers()
    comparisons = []

    for item in evidence.get("observations", [])[:500]:
        if not isinstance(item, dict):
            continue
        service_id_display = str(item.get("service_id") or "").strip()
        service_id = canonical_service_id(service_id_display)
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
        known_matches = [
            fact for fact in facts
            if isinstance(fact, dict)
            and fact.get("kind") != "supervisor_reported_engine_status"
            and service_id in _identity_values(fact)
        ]
        unknown_matches = [
            unknown for unknown in unknowns
            if isinstance(unknown, dict) and service_id in _identity_values(unknown)
        ]
        matches = known_matches + unknown_matches
        comparison = {
            "service_id": service_id_display,
            "canonical_service_id": service_id,
            "supervisor_status": item.get("status", "unknown"),
            "director_status": None,
            "result": "unmatched",
            "identity_match": "canonical_exact",
            "director_registry_status": (
                "declared" if service_id in declared_observers else "not_declared"
            ),
            "independent_verification": False,
        }
        if len(matches) == 1:
            matched = matches[0]
            director_status = matched.get("status", matched.get("reported_status", "unknown"))
            comparison["director_status"] = director_status
            comparison["director_evidence_state"] = "known_fact" if matched in known_matches else "explicit_unknown"
            supervisor_class, director_class = _status_class(item.get("status")), _status_class(director_status)
            comparison["status_classes"] = {
                "supervisor": supervisor_class,
                "director": director_class,
            }
            comparison["matched_record_kind"] = str(matched.get("kind") or "unspecified")
            comparison["matching_fact_count"] = len(known_matches)
            comparison["matching_unknown_count"] = len(unknown_matches)
            if "unknown" in {supervisor_class, director_class}:
                comparison["result"] = "unknown"
                unknowns.append({
                    "kind": "supervisor_director_status_comparison_unknown",
                    "observer": service_id_display,
                    "supervisor_status": item.get("status"),
                    "director_status": director_status,
                })
            elif supervisor_class != director_class:
                comparison["result"] = "disagreement"
                conflicts.append({
                    "kind": "supervisor_director_status_disagreement",
                    "observer": service_id_display,
                    "supervisor_status": item.get("status"),
                    "director_status": director_status,
                    "interpretation": (
                        "Meta Engine and Director report different statuses for the same "
                        "canonical service identity. Neither report is silently preferred."
                    ),
                })
            else:
                comparison["result"] = "agreement"
            comparison["coverage_status"] = (
                "matched_explicit_unknown" if comparison["director_evidence_state"] == "explicit_unknown"
                else "matched_comparable_fact"
            )
        elif len(matches) > 1:
            comparison["result"] = "ambiguous"
            comparison["coverage_status"] = "ambiguous_identity"
            unknowns.append({
                "kind": "supervisor_observer_identity_ambiguous",
                "observer": service_id_display,
                "matching_director_facts": len(matches),
            })
        else:
            comparison["coverage_status"] = (
                "declared_no_comparable_evidence" if service_id in declared_observers
                else "no_director_observer_declared"
            )
            unknowns.append({
                "kind": "supervisor_observation_not_matched",
                "observer": service_id_display,
                "coverage_status": comparison["coverage_status"],
                "interpretation": (
                    "The canonical service is declared in the Director observer registry, "
                    "but no matching current fact was available."
                    if service_id in declared_observers else
                    "No Director observer is declared for this canonical service identity."
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

    results = [item["result"] for item in comparisons]
    counts = {
        "compared": len(comparisons),
        "comparable_status_pairs": sum(value in {"agreement", "disagreement"} for value in results),
        "agreements": results.count("agreement"),
        "disagreements": results.count("disagreement"),
        "unmatched": results.count("unmatched"),
        "ambiguous": results.count("ambiguous"),
        "unknown": results.count("unknown"),
    }
    coverage_counts = {
        "supervisor_observations": len(comparisons),
        "director_registry_declared": len(declared_observers),
        "matched_comparable_fact": sum(item["coverage_status"] == "matched_comparable_fact" for item in comparisons),
        "matched_explicit_unknown": sum(item["coverage_status"] == "matched_explicit_unknown" for item in comparisons),
        "declared_no_comparable_evidence": sum(item["coverage_status"] == "declared_no_comparable_evidence" for item in comparisons),
        "no_director_observer_declared": sum(item["coverage_status"] == "no_director_observer_declared" for item in comparisons),
        "ambiguous_identity": sum(item["coverage_status"] == "ambiguous_identity" for item in comparisons),
    }
    reconciliation_status = (
        "unavailable" if status not in {"complete", "partial"} else
        "disagreement" if counts["disagreements"] else
        "partial" if counts["unmatched"] or counts["ambiguous"] or counts["unknown"] or status == "partial" else
        "agreement" if counts["compared"] else "no_comparable_observations"
    )
    context["facts"], context["unknowns"], context["conflicts"] = facts, unknowns, conflicts
    context["supervisor_observation_evidence"] = evidence
    context["supervisor_evidence_reconciliation"] = {
        "status": reconciliation_status,
        "counts": counts,
        "coverage": coverage_counts,
        "comparisons": comparisons,
        "identity_contract": "director.service_identity/v1",
        "identity_aliases": dict(CANONICAL_SERVICE_ALIASES),
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
