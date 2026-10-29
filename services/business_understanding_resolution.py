"""V65.4 deterministic/AI disagreement resolution.

This is an interpretation-quality boundary, not an authorization boundary.
When model and deterministic observations disagree, KDO records the conflict,
uses explicit fail-closed rules, and preserves both observations for audit.
"""
from __future__ import annotations
from typing import Any

SCHEMA_VERSION = "1.0"
ALLOWED_INTENTS = {
    "purchase_interest", "price_request", "meeting_request", "objection",
    "opt_out", "general_inquiry",
}


def _conf(v: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(v))), 3)
    except (TypeError, ValueError):
        return 0.0


def _verified_evidence(ai: dict[str, Any] | None, text: str) -> list[str]:
    if not isinstance(ai, dict):
        return []
    raw = ai.get("evidence") or []
    if isinstance(raw, str): raw = [raw]
    if not isinstance(raw, list): return []
    normalized = text.lower()
    out = []
    for item in raw[:5]:
        excerpt = str(item).strip()[:240]
        if excerpt and excerpt.lower() in normalized and excerpt not in out:
            out.append(excerpt)
    return out


def resolve_business_understanding(
    *, deterministic: dict[str, Any] | None,
    ai: dict[str, Any] | None,
    text: str,
) -> dict[str, Any]:
    """Resolve interpretation disagreement with explicit, auditable rules.

    Rule order:
      1. Agreement -> consensus.
      2. No usable AI -> deterministic_only.
      3. AI opt-out with exact evidence and >= .70 -> safety_sensitive_ai.
      4. AI may replace deterministic only with exact evidence and >= .15
         confidence advantage.
      5. Otherwise deterministic wins and conflict remains reviewable.

    No result grants action authority.
    """
    det = deterministic if isinstance(deterministic, dict) else {}
    ai_obj = ai if isinstance(ai, dict) and ai.get("ai_used") else None
    det_intent = str(det.get("intent") or "general_inquiry")
    ai_intent = str(ai_obj.get("intent") or "") if ai_obj else ""
    det_conf = _conf(det.get("confidence"))
    ai_conf = _conf(ai_obj.get("confidence")) if ai_obj else 0.0
    ai_evidence = _verified_evidence(ai_obj, text)

    if det_intent not in ALLOWED_INTENTS:
        det_intent = "general_inquiry"
    if ai_intent not in ALLOWED_INTENTS:
        ai_obj = None
        ai_intent = ""
        ai_conf = 0.0
        ai_evidence = []

    if not ai_obj:
        return {
            "schema_version": SCHEMA_VERSION, "status": "deterministic_only",
            "disagreement": False, "severity": "none", "deterministic_intent": det_intent,
            "deterministic_confidence": det_conf, "ai_intent": None, "ai_confidence": None,
            "confidence_gap": None, "ai_verified_evidence_count": 0,
            "selected_intent": det_intent, "selected_source": "deterministic",
            "reason": "AI interpretation unavailable or invalid; deterministic interpretation retained.",
            "authority": "none",
        }

    disagreement = det_intent != ai_intent
    gap = round(ai_conf - det_conf, 3)
    if not disagreement:
        return {
            "schema_version": SCHEMA_VERSION, "status": "consensus",
            "disagreement": False, "severity": "none", "deterministic_intent": det_intent,
            "deterministic_confidence": det_conf, "ai_intent": ai_intent, "ai_confidence": ai_conf,
            "confidence_gap": gap, "ai_verified_evidence_count": len(ai_evidence),
            "selected_intent": ai_intent if ai_conf >= det_conf else det_intent,
            "selected_source": "ai" if ai_conf >= det_conf else "deterministic",
            "reason": "AI and deterministic interpretations agree on intent.", "authority": "none",
        }

    severity = "high" if "opt_out" in {det_intent, ai_intent} else "medium"
    if ai_intent == "opt_out" and ai_conf >= 0.70 and ai_evidence:
        selected_intent, source, status = ai_intent, "ai_safety_sensitive", "safety_sensitive_ai"
        reason = "AI detected opt-out with exact customer evidence; retain as a safety-sensitive interpretation candidate."
    elif ai_evidence and gap >= 0.10:
        selected_intent, source, status = ai_intent, "ai_disagreement_resolution", "ai_supported_resolution"
        reason = "AI has verified customer evidence and a material confidence advantage over the deterministic interpretation."
    else:
        selected_intent, source, status = det_intent, "deterministic_disagreement_resolution", "deterministic_preferred"
        reason = "Conflict did not meet the evidence and confidence threshold required to replace deterministic interpretation."

    return {
        "schema_version": SCHEMA_VERSION, "status": status, "disagreement": True,
        "severity": severity, "deterministic_intent": det_intent,
        "deterministic_confidence": det_conf, "ai_intent": ai_intent,
        "ai_confidence": ai_conf, "confidence_gap": gap,
        "ai_verified_evidence_count": len(ai_evidence), "selected_intent": selected_intent,
        "selected_source": source, "reason": reason, "authority": "none",
    }


def validate_resolution(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported business understanding resolution schema")
    if value.get("status") not in {
        "deterministic_only", "consensus", "safety_sensitive_ai",
        "ai_supported_resolution", "deterministic_preferred",
    }:
        raise ValueError("invalid business understanding resolution status")
    if value.get("severity") not in {"none", "medium", "high"}:
        raise ValueError("invalid disagreement severity")
    if value.get("authority") != "none":
        raise ValueError("resolution cannot grant authority")
    if bool(value.get("disagreement")) and value.get("severity") == "none":
        raise ValueError("disagreement requires non-none severity")
