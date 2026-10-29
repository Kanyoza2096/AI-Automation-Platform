"""R13-I provider closure planner.

Evaluates whether an existing provider candidate can *prove* the semantic and
verification requirements of an already-defined Sector Platform provider gap.
This is a read-only planning surface. It does not create bindings, activate
connectors, authorize actions, call providers, or replace the Action Kernel /
Connector Fabric.
"""
from __future__ import annotations

from typing import Any, Iterable

from services.sector_provider_gap_requirements import GAPS


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _gap_index(gaps: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(g.get("sector_action")): dict(g) for g in gaps if g.get("sector_action")}


def _candidate_status(gap: dict[str, Any], candidate: dict[str, Any]) -> tuple[str, list[str]]:
    missing: list[str] = []
    if not bool(candidate.get("semantic_match")):
        return "SEMANTIC_MISMATCH", ["SEMANTIC_CAPABILITY_PROOF"]

    if _norm(candidate.get("provider_capability")) != _norm(gap.get("required_capability")):
        missing.append("REQUIRED_CAPABILITY_PROOF")
    if not candidate.get("provider_receipt"):
        missing.append("PROVIDER_RECEIPT")
    if not candidate.get("authoritative_verification"):
        missing.append("AUTHORITATIVE_VERIFICATION")
    if not candidate.get("reconciliation"):
        missing.append("RECONCILIATION")
    if not candidate.get("identity_mapping"):
        missing.append("IDENTITY_MAPPING")
    if candidate.get("certification_status") != "ACTIVE":
        missing.append("ACTIVE_CERTIFIED_PROVIDER")

    declared = set(candidate.get("satisfied_gates") or ())
    required = set(gap.get("minimum_binding_gates") or ())
    missing.extend(sorted(required - declared))
    missing = list(dict.fromkeys(missing))
    return ("CLOSED" if not missing else "CANDIDATE"), missing


def plan_provider_closure(
    *,
    gaps: Iterable[dict[str, Any]] | None = None,
    candidates: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return an explainable closure assessment for frozen provider gaps.

    Candidate records are descriptive evidence supplied by an existing provider
    registry/connector review. No candidate is promoted to BOUND/CERTIFIED/ACTIVE
    by this function.
    """
    gap_map = _gap_index(gaps or GAPS)
    candidate_list = [dict(c) for c in (candidates or ()) if isinstance(c, dict)]
    by_action: dict[str, list[dict[str, Any]]] = {key: [] for key in gap_map}

    for candidate in candidate_list:
        action = str(candidate.get("sector_action") or "")
        if action not in gap_map:
            continue
        status, missing = _candidate_status(gap_map[action], candidate)
        by_action[action].append({
            "provider": candidate.get("provider"),
            "governed_provider_action": candidate.get("governed_provider_action"),
            "status": status,
            "missing_proofs": missing,
            "semantic_match": bool(candidate.get("semantic_match")),
        })

    assessments: list[dict[str, Any]] = []
    for action, gap in gap_map.items():
        matches = by_action[action]
        if any(m["status"] == "CLOSED" for m in matches):
            state = "READY_FOR_EXISTING_BINDING_REVIEW"
            reason = "candidate_proves_all_declared_closure gates; existing binding/certification authorities must still perform promotion"
        elif any(m["status"] == "CANDIDATE" for m in matches):
            state = "PROVIDER_GAP_REMAINS"
            reason = "a semantically plausible provider exists but required proof gates remain"
        elif matches:
            state = "NO_SEMANTIC_MATCH"
            reason = "reviewed candidates do not prove the required provider semantics"
        else:
            state = "NO_CANDIDATE"
            reason = "no provider candidate supplied for this required capability"

        assessments.append({
            "sector_action": action,
            "required_capability": gap.get("required_capability"),
            "state": state,
            "reason": reason,
            "candidates": matches,
            "required_gates": list(gap.get("minimum_binding_gates") or ()),
        })

    return {
        "schema": "KDO.R13I.ProviderClosurePlan/v1",
        "read_only": True,
        "authority": {
            "binding_authority": "existing_sector_provider_binding_contracts",
            "certification_authority": "existing_connector_certification",
            "execution_authority": "existing_action_kernel_and_connector_fabric",
        },
        "summary": {
            "gaps": len(assessments),
            "ready_for_binding_review": sum(a["state"] == "READY_FOR_EXISTING_BINDING_REVIEW" for a in assessments),
            "provider_gaps_remaining": sum(a["state"] == "PROVIDER_GAP_REMAINS" for a in assessments),
            "no_semantic_match": sum(a["state"] == "NO_SEMANTIC_MATCH" for a in assessments),
            "no_candidate": sum(a["state"] == "NO_CANDIDATE" for a in assessments),
        },
        "assessments": assessments,
        "safety": {
            "does_not_create_binding": True,
            "does_not_certify_provider": True,
            "does_not_activate_connector": True,
            "does_not_authorize_action": True,
            "does_not_execute_provider": True,
            "does_not_replace_action_kernel": True,
            "does_not_replace_connector_fabric": True,
        },
    }
