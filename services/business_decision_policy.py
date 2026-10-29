"""V65.7 formal decision-policy evaluation.

Evaluates a bounded V65.6 decision candidate into an auditable disposition.
This layer grants no authority and performs no side effects.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
DISPOSITIONS = {"ALLOW", "APPROVAL_REQUIRED", "REVIEW", "BLOCK"}

# Policy is KDO-owned and deliberately narrower than the candidate registry.
# A candidate action can only be ALLOWed when its risk/governance constraints
# satisfy this matrix. Unknown actions fail closed.
ACTION_POLICY: dict[str, dict[str, Any]] = {
    "crm_schedule_followup": {"risk": "medium", "disposition": "ALLOW", "approval": False},
    "notify_operator": {"risk": "low", "disposition": "ALLOW", "approval": False},
    "crm_add_meeting": {"risk": "medium", "disposition": "ALLOW", "approval": False},
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def evaluate_decision_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    from services.business_decision_candidate import validate_decision_candidate
    validate_decision_candidate(candidate)

    action = str(candidate["candidate_action"])
    status = str(candidate["status"])
    risk = str(candidate.get("risk_level") or "critical").lower()
    governance = str(candidate.get("governance_status") or "")
    policy = ACTION_POLICY.get(action)

    if policy is None:
        disposition, reason = "BLOCK", "candidate_action_not_allowlisted_by_decision_policy"
    elif status == "blocked":
        disposition, reason = "BLOCK", "candidate_is_blocked_by_understanding_governance"
    elif status == "review_required":
        disposition, reason = "REVIEW", "understanding_requires_review_before_decision"
    elif governance != "decision_eligible":
        disposition, reason = "REVIEW", "decision_input_is_not_decision_eligible"
    elif candidate.get("disagreement") and candidate.get("resolution_status") in {None, "unresolved", "review_required"}:
        disposition, reason = "REVIEW", "ai_deterministic_disagreement_is_not_resolved"
    elif risk not in RISK_ORDER:
        disposition, reason = "BLOCK", "unknown_risk_level_fails_closed"
    elif RISK_ORDER[risk] > RISK_ORDER[str(policy["risk"])]:
        disposition, reason = "APPROVAL_REQUIRED", "candidate_risk_exceeds_action_policy_baseline"
    elif bool(candidate.get("requires_approval")) or bool(policy.get("approval")):
        disposition, reason = "APPROVAL_REQUIRED", "policy_requires_approval"
    else:
        disposition, reason = str(policy["disposition"]), "candidate_satisfies_current_decision_policy"

    # A disposition is an auditable policy result, not permission to execute.
    result = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": "dpe_" + str(candidate["candidate_id"])[3:23],
        "candidate_id": candidate["candidate_id"],
        "understanding_id": candidate["understanding_id"],
        "workspace_id": candidate["workspace_id"],
        "customer_id": candidate.get("customer_id"),
        "action": action,
        "intent": candidate.get("intent"),
        "message_id": candidate.get("message_id"),
        "risk_level": risk,
        "policy_version": "v65.7.1",
        "disposition": disposition,
        "reason": reason,
        "approval_required": disposition == "APPROVAL_REQUIRED",
        "review_required": disposition == "REVIEW",
        "governance_status": governance,
        "candidate_status": status,
        "constraints": list(candidate.get("constraints") or [])[:20],
        "objective_id": candidate.get("objective_id"),
        "objective_context": candidate.get("objective_context"),
        "objective_binding_status": candidate.get("objective_binding_status", "unbound"),
        "authority": "none",
    }
    validate_decision_policy_evaluation(result)
    return result


def validate_decision_policy_evaluation(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported decision policy evaluation schema")
    for key in ("evaluation_id", "candidate_id", "understanding_id", "workspace_id", "action", "disposition", "reason"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value.get("disposition") not in DISPOSITIONS:
        raise ValueError("invalid decision disposition")
    if value.get("authority") != "none":
        raise ValueError("policy evaluation cannot grant authority")
    if value.get("approval_required") != (value.get("disposition") == "APPROVAL_REQUIRED"):
        raise ValueError("approval_required must match disposition")
    if value.get("review_required") != (value.get("disposition") == "REVIEW"):
        raise ValueError("review_required must match disposition")
