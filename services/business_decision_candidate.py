"""V65.6 bounded decision candidate projection.

This module is the last V65 interpretation-adjacent layer. It converts a trusted
V65.5 decision input into a deterministic *candidate* for KDO's decision plane.
A candidate is not a decision, approval, or execution command.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"

# Only known business operations may be proposed here. Unknown intent never
# becomes an executable action name.
POLICY: dict[str, dict[str, Any]] = {
    "purchase_interest": {
        "action": "crm_schedule_followup",
        "risk": "medium",
        "requires_approval": False,
        "reason": "Customer expressed purchase interest; preserve momentum with governed sales follow-up.",
    },
    "price_request": {
        "action": "notify_operator",
        "risk": "medium",
        "requires_approval": False,
        "reason": "Customer requested pricing; route the commercial request for attention.",
    },
    "meeting_request": {
        "action": "crm_add_meeting",
        "risk": "medium",
        "requires_approval": False,
        "reason": "Customer requested a meeting; create governed scheduling work.",
    },
    "objection": {
        "action": "notify_operator",
        "risk": "medium",
        "requires_approval": False,
        "reason": "Customer raised an objection; expose the signal for human commercial review.",
    },
    "opt_out": {
        "action": "notify_operator",
        "risk": "high",
        "requires_approval": False,
        "reason": "Customer requested no further contact; prioritize suppression review.",
    },
    "general_inquiry": {
        "action": "notify_operator",
        "risk": "low",
        "requires_approval": False,
        "reason": "No stronger bounded business intent was established; request human review.",
    },
}


def build_decision_candidate(decision_input: dict[str, Any]) -> dict[str, Any]:
    from services.business_understanding_decision_input import validate_decision_input
    validate_decision_input(decision_input)

    intent = str(decision_input.get("intent") or "general_inquiry").strip().lower()
    policy = POLICY.get(intent)
    if policy is None:
        policy = POLICY["general_inquiry"]
        intent = "general_inquiry"

    eligible = bool(decision_input.get("decision_eligible"))
    governance_status = str(decision_input.get("governance_status"))
    if eligible:
        status = "proposed"
    elif governance_status == "needs_review":
        status = "review_required"
    else:
        status = "blocked"

    # A disagreement is a first-class uncertainty. It must remain visible to the
    # decision plane rather than being silently treated as consensus.
    constraints = ["candidate_is_not_authorization", "action_must_pass_policy_and_action_kernel"]
    if decision_input.get("disagreement"):
        constraints.append("ai_deterministic_disagreement_present")
    if governance_status != "decision_eligible":
        constraints.append("understanding_not_decision_eligible")
    if intent == "opt_out":
        constraints.append("suppression_signal_requires_priority_review")

    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": "dc_" + str(decision_input["understanding_id"])[3:23],
        "understanding_id": decision_input["understanding_id"],
        "message_id": decision_input.get("message_id"),
        "workspace_id": decision_input["workspace_id"],
        "customer_id": decision_input.get("customer_id"),
        "channel": decision_input.get("channel"),
        "correlation_id": decision_input.get("correlation_id"),
        "intent": intent,
        "signals": list(decision_input.get("signals") or [])[:20],
        "candidate_action": policy["action"],
        "risk_level": policy["risk"],
        "requires_approval": bool(policy["requires_approval"]),
        "status": status,
        "reason": policy["reason"],
        "intent_confidence": float(decision_input.get("intent_confidence") or 0.0),
        "governance_score": float(decision_input.get("governance_score") or 0.0),
        "governance_status": governance_status,
        "resolution_status": decision_input.get("resolution_status"),
        "disagreement": bool(decision_input.get("disagreement")),
        "constraints": constraints,
        "objective_id": decision_input.get("objective_id"),
        "objective_context": decision_input.get("objective_context"),
        "objective_binding_status": decision_input.get("objective_binding_status", "unbound"),
        "authority": "none",
    }


def validate_decision_candidate(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported business decision candidate schema")
    for key in ("candidate_id", "understanding_id", "workspace_id", "candidate_action", "status"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value.get("status") not in {"proposed", "review_required", "blocked"}:
        raise ValueError("invalid candidate status")
    if value.get("authority") != "none":
        raise ValueError("decision candidate cannot grant authority")
    if not 0 <= float(value.get("intent_confidence", -1)) <= 1:
        raise ValueError("intent_confidence must be between 0 and 1")
    if not 0 <= float(value.get("governance_score", -1)) <= 1:
        raise ValueError("governance_score must be between 0 and 1")
    if value.get("status") == "proposed" and value.get("governance_status") != "decision_eligible":
        raise ValueError("proposed candidate requires decision eligibility")
