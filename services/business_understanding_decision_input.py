"""V65.5 decision-plane projection for business understanding.

The interpretation layer produces observations. This module creates the only
contract that V65 automation is allowed to consume as a business signal.
It deliberately strips AI/provider details and carries no action authority.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
ALLOWED_GOVERNANCE = {"decision_eligible", "needs_review", "insufficient_evidence"}


def build_decision_input(understanding: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(understanding, dict):
        raise ValueError("business understanding must be an object")
    from services.business_understanding import validate_business_understanding
    validate_business_understanding(understanding)

    governance = understanding["governance"]
    resolution = understanding["resolution"]
    status = str(governance.get("status") or "insufficient_evidence")
    if status not in ALLOWED_GOVERNANCE:
        raise ValueError("invalid governance status")

    # This is a projection, not a copy. Provider/model/rationale fields and raw
    # interpretation details stay outside the decision contract.
    return {
        "schema_version": SCHEMA_VERSION,
        "understanding_id": str(understanding.get("understanding_id") or ""),
        "message_id": understanding.get("message_id"),
        "workspace_id": understanding.get("workspace_id"),
        "customer_id": understanding.get("customer_id"),
        "channel": understanding.get("channel"),
        "correlation_id": understanding.get("correlation_id"),
        "intent": understanding.get("intent"),
        "intent_confidence": float(understanding.get("intent_confidence") or 0.0),
        "signals": list(understanding.get("signals") or [])[:20],
        "governance_status": status,
        "decision_eligible": bool(governance.get("decision_eligible")),
        "interpretation_risk": governance.get("interpretation_risk"),
        "governance_score": float(governance.get("governance_score") or 0.0),
        "resolution_status": resolution.get("status"),
        "disagreement": bool(resolution.get("disagreement")),
        "authority": "none",
    }


def validate_decision_input(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported business decision input schema")
    if not value.get("understanding_id"):
        raise ValueError("understanding_id is required")
    if not value.get("workspace_id"):
        raise ValueError("workspace_id is required")
    if value.get("governance_status") not in ALLOWED_GOVERNANCE:
        raise ValueError("invalid governance_status")
    if not isinstance(value.get("signals"), list):
        raise ValueError("signals must be an array")
    if not 0 <= float(value.get("intent_confidence", -1)) <= 1:
        raise ValueError("intent_confidence must be between 0 and 1")
    if not 0 <= float(value.get("governance_score", -1)) <= 1:
        raise ValueError("governance_score must be between 0 and 1")
    if value.get("authority") != "none":
        raise ValueError("decision input cannot grant authority")
    eligible = value.get("governance_status") == "decision_eligible"
    if bool(value.get("decision_eligible")) != eligible:
        raise ValueError("decision_eligible must match governance_status")
