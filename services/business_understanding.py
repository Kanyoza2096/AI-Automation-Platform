"""Canonical V65 business-understanding contract.

AI and deterministic interpreters produce observations. This module normalizes
those observations into a versioned, evidence-backed object consumed by KDO.
It contains no action execution and no policy authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

SCHEMA_VERSION = "1.0"
ALLOWED_INTENTS = {
    "purchase_interest", "price_request", "meeting_request", "objection",
    "opt_out", "general_inquiry",
}
SIGNAL_NAMES = {
    "purchase_interest", "price_request", "meeting_request", "objection",
    "opt_out", "general_inquiry",
}


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def _evidence(items: Any, text: str) -> list[dict[str, Any]]:
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        items = []
    normalized = text.lower()
    result: list[dict[str, Any]] = []
    for raw in items[:10]:
        excerpt = str(raw).strip()[:240]
        if excerpt and excerpt.lower() in normalized:
            result.append({"text": excerpt, "source": "customer_message", "verified": True})
    return result


def build_business_understanding(
    *,
    text: str,
    deterministic: dict[str, Any] | None = None,
    ai: dict[str, Any] | None = None,
    message_id: str | None = None,
    workspace_id: str | None = None,
    customer_id: str | None = None,
    channel: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build a side-effect-free, auditable understanding object.

    AI never receives authority from this function. If AI is present it is
    recorded as an interpretation source; downstream KDO remains responsible
    for deciding whether any action is allowed.
    """
    text = str(text or "").strip()
    deterministic = deterministic or {}
    ai = ai if isinstance(ai, dict) else None

    base_intent = str(deterministic.get("intent") or "general_inquiry")
    base_confidence = _confidence(deterministic.get("confidence"))
    signals = [str(s) for s in (deterministic.get("signals") or []) if str(s) in SIGNAL_NAMES]

    from services.business_understanding_resolution import resolve_business_understanding
    resolution = resolve_business_understanding(deterministic=deterministic, ai=ai, text=text)
    selected_intent = resolution["selected_intent"]
    selected_source = resolution["selected_source"]
    ai_evidence = _evidence(ai.get("evidence"), text) if ai else []
    deterministic_evidence = _evidence(deterministic.get("evidence"), text)
    use_ai = selected_source in {"ai", "ai_safety_sensitive", "ai_disagreement_resolution"}
    selected = {
        "intent": selected_intent if selected_intent in ALLOWED_INTENTS else "general_inquiry",
        "confidence": _confidence(ai.get("confidence")) if use_ai and ai else base_confidence,
        "source": "ai" if use_ai else "deterministic",
        "evidence": ai_evidence if use_ai else deterministic_evidence,
        "rationale": (str(ai.get("rationale") or "")[:500] if use_ai and ai else "Deterministic interpretation."),
    }
    if resolution.get("disagreement"):
        signals = list(dict.fromkeys([resolution["deterministic_intent"], resolution["ai_intent"], *signals]))

    if selected["intent"] not in signals:
        signals.insert(0, selected["intent"])
    signals = list(dict.fromkeys(signals))[:10]

    uncertainty: list[str] = []
    if selected["confidence"] < 0.75:
        uncertainty.append("low_interpretation_confidence")
    if selected["source"] == "ai" and not selected["evidence"]:
        uncertainty.append("ai_interpretation_without_verified_evidence")
    if len(signals) > 1:
        uncertainty.append("multiple_business_signals_present")
    if resolution.get("disagreement"):
        uncertainty.append("ai_deterministic_disagreement")
        if resolution.get("status") == "deterministic_preferred":
            uncertainty.append("deterministic_preferred_after_disagreement")

    result = {
        "understanding_id": "bu_" + uuid.uuid4().hex[:20],
        "schema_version": SCHEMA_VERSION,
        "message_id": str(message_id or "") or None,
        "workspace_id": str(workspace_id or "") or None,
        "customer_id": str(customer_id or "") or None,
        "channel": str(channel or "") or None,
        "correlation_id": str(correlation_id or message_id or "") or None,
        "intent": selected["intent"],
        "intent_confidence": selected["confidence"],
        "signals": signals,
        "evidence": selected["evidence"],
        "uncertainties": uncertainty,
        "rationale": selected["rationale"],
        "interpretation_source": selected["source"],
        "ai_used": bool(ai and ai.get("ai_used")),
        "model": (str(ai.get("model")) if ai and ai.get("model") else None),
        "provider": (str(ai.get("provider")) if ai and ai.get("provider") else "deterministic"),
        "resolution": resolution,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # V65.3 evaluates trust separately from decision authority.
    from services.business_understanding_governance import govern_business_understanding
    result["governance"] = govern_business_understanding(result)
    return result


def validate_business_understanding(value: dict[str, Any]) -> None:
    """Fail closed on malformed canonical understanding objects."""
    if not isinstance(value, dict):
        raise ValueError("business understanding must be an object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported business understanding schema_version")
    if value.get("intent") not in ALLOWED_INTENTS:
        raise ValueError("invalid business understanding intent")
    confidence = value.get("intent_confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("intent_confidence must be between 0 and 1")
    if not isinstance(value.get("signals"), list) or not isinstance(value.get("evidence"), list):
        raise ValueError("signals and evidence must be arrays")
    for item in value["evidence"]:
        if not isinstance(item, dict) or not item.get("verified"):
            raise ValueError("all evidence must be verified")
    resolution = value.get("resolution")
    if not isinstance(resolution, dict):
        raise ValueError("resolution is required")
    from services.business_understanding_resolution import validate_resolution
    validate_resolution(resolution)
    governance = value.get("governance")
    if not isinstance(governance, dict):
        raise ValueError("governance is required")
    from services.business_understanding_governance import validate_governance
    validate_governance(governance)
