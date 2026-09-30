"""V65.3 evidence and confidence governance.

This module evaluates the quality and actionability of an interpretation. It
never authorizes, approves, or executes an action. A governed interpretation
is merely allowed to reach KDO's decision plane with an explicit trust state.
"""
from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"

# These are interpretation-risk classes, not action permissions. Policy remains
# the only authority for side effects.
SIGNAL_RISK = {
    "purchase_interest": "low",
    "price_request": "medium",
    "meeting_request": "medium",
    "objection": "medium",
    "opt_out": "high",
    "general_inquiry": "low",
}

RISK_WEIGHTS = {"low": 1.0, "medium": 1.15, "high": 1.35}


def _clamp(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def _evidence_score(evidence: Any) -> tuple[float, list[str]]:
    if not isinstance(evidence, list):
        return 0.0, ["no_verified_evidence"]
    verified = [e for e in evidence if isinstance(e, dict) and e.get("verified")]
    if not verified:
        return 0.0, ["no_verified_evidence"]
    # One verified exact customer excerpt is meaningful; additional distinct
    # excerpts improve corroboration but have diminishing returns.
    distinct = {str(e.get("text", "")).strip().lower() for e in verified if str(e.get("text", "")).strip()}
    score = min(1.0, 0.55 + 0.15 * max(0, len(distinct) - 1))
    return round(score, 3), ["verified_customer_evidence"]


def _context_score(understanding: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if understanding.get("message_id"):
        score += 0.25
    else:
        reasons.append("missing_message_id")
    if understanding.get("workspace_id"):
        score += 0.25
    else:
        reasons.append("missing_workspace_id")
    if understanding.get("channel"):
        score += 0.15
    else:
        reasons.append("missing_channel")
    if understanding.get("correlation_id"):
        score += 0.15
    else:
        reasons.append("missing_correlation_id")
    if understanding.get("intent"):
        score += 0.20
    else:
        reasons.append("missing_intent")
    return round(score, 3), reasons


def govern_business_understanding(understanding: dict[str, Any]) -> dict[str, Any]:
    """Return a fail-closed governance assessment for a canonical understanding.

    The returned status controls confidence/evidence gating only. It is not an
    approval decision and must never be interpreted as authorization to act.
    """
    if not isinstance(understanding, dict):
        raise ValueError("business understanding must be an object")

    intent = str(understanding.get("intent") or "general_inquiry")
    risk = SIGNAL_RISK.get(intent, "high")
    confidence = _clamp(understanding.get("intent_confidence"))
    evidence_score, evidence_reasons = _evidence_score(understanding.get("evidence"))
    context_score, context_reasons = _context_score(understanding)
    uncertainties = [str(x) for x in (understanding.get("uncertainties") or [])]

    # Confidence is deliberately only one component of trust. A high model
    # score cannot compensate for absent evidence or missing context.
    composite = round(
        (confidence * 0.50) + (evidence_score * 0.30) + (context_score * 0.20), 3
    )

    reasons = list(evidence_reasons) + list(context_reasons)
    if uncertainties:
        reasons.extend(uncertainties[:10])

    # Opt-out is high interpretation risk but should not be suppressed merely
    # because the model confidence is imperfect when exact evidence exists.
    min_confidence = 0.70 if intent == "opt_out" else 0.75
    min_composite = 0.68 if intent == "opt_out" else 0.72

    if confidence == 0.0 or evidence_score == 0.0:
        status = "insufficient_evidence"
    elif confidence < min_confidence or composite < min_composite:
        status = "needs_review"
    elif uncertainties and "ai_interpretation_without_verified_evidence" in uncertainties:
        status = "insufficient_evidence"
    else:
        status = "decision_eligible"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "decision_eligible": status == "decision_eligible",
        "interpretation_risk": risk,
        "confidence_score": confidence,
        "evidence_score": evidence_score,
        "context_score": context_score,
        "governance_score": composite,
        "min_confidence": min_confidence,
        "min_governance_score": min_composite,
        "reasons": list(dict.fromkeys(reasons))[:20],
        "authority": "none",
    }


def validate_governance(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported interpretation governance schema")
    if value.get("status") not in {"decision_eligible", "needs_review", "insufficient_evidence"}:
        raise ValueError("invalid interpretation governance status")
    for key in ("confidence_score", "evidence_score", "context_score", "governance_score"):
        if not 0 <= float(value.get(key, -1)) <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if value.get("authority") != "none":
        raise ValueError("interpretation governance cannot grant authority")
    if bool(value.get("decision_eligible")) != (value.get("status") == "decision_eligible"):
        raise ValueError("decision_eligible must match governance status")
