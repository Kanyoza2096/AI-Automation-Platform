"""V65.2 canonical business-signal normalization.

Turns model/rule-specific interpretations into KDO-owned semantic signals.
This module is pure interpretation: it never executes actions or grants authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

SCHEMA_VERSION = "1.0"

SIGNAL_DEFINITIONS: dict[str, dict[str, str]] = {
    "PURCHASE_INTENT": {"family": "commercial", "description": "Customer expresses intent or readiness to purchase."},
    "PRICE_SENSITIVITY": {"family": "commercial", "description": "Customer indicates price/cost is material to the decision."},
    "PRICING_REQUEST": {"family": "commercial", "description": "Customer requests price, cost, quote, or pricing information."},
    "NEGOTIATION_REQUEST": {"family": "commercial", "description": "Customer asks for a better price, terms, discount, or negotiation."},
    "MEETING_REQUEST": {"family": "engagement", "description": "Customer requests a call, meeting, demo, or scheduled conversation."},
    "OBJECTION": {"family": "commercial", "description": "Customer expresses resistance, hesitation, or a barrier to proceeding."},
    "OPT_OUT": {"family": "compliance", "description": "Customer requests that contact or messaging stop."},
    "GENERAL_INQUIRY": {"family": "engagement", "description": "Customer asks a general question without a stronger canonical signal."},
}


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def _signal(name: str, confidence: float, *, evidence: list[dict[str, Any]], source: str, reason: str) -> dict[str, Any]:
    definition = SIGNAL_DEFINITIONS[name]
    return {
        "name": name,
        "family": definition["family"],
        "confidence": _confidence(confidence),
        "evidence": evidence[:5],
        "source": source,
        "reason": reason[:300],
    }


def normalize_business_signals(understanding: dict[str, Any]) -> dict[str, Any]:
    """Create a stable KDO-owned semantic signal projection.

    Input may contain model/provider intent labels, but output names are owned by
    KDO. Multiple signals are preserved; opt-out is explicitly retained as the
    highest-priority compliance signal without authorizing any action.
    """
    if not isinstance(understanding, dict):
        raise ValueError("business understanding must be an object")

    intent = str(understanding.get("intent") or "general_inquiry")
    confidence = _confidence(understanding.get("intent_confidence"))
    evidence = understanding.get("evidence") if isinstance(understanding.get("evidence"), list) else []
    evidence = [x for x in evidence if isinstance(x, dict) and x.get("verified")]
    raw_signals = understanding.get("signals") if isinstance(understanding.get("signals"), list) else []
    names = {str(x).strip() for x in raw_signals}
    names.add(intent)

    mapping = {
        "purchase_interest": ["PURCHASE_INTENT"],
        "price_request": ["PRICING_REQUEST"],
        "meeting_request": ["MEETING_REQUEST"],
        "objection": ["OBJECTION"],
        "opt_out": ["OPT_OUT"],
        "general_inquiry": ["GENERAL_INQUIRY"],
    }
    out: list[dict[str, Any]] = []
    for source_name in names:
        for canonical in mapping.get(source_name, []):
            out.append(_signal(canonical, confidence, evidence=evidence, source="understanding", reason=f"Normalized from {source_name}."))

    # Cross-signal inference is deliberately conservative: it only derives
    # business semantics when the originating signals are already explicit.
    if "price_request" in names:
        out.append(_signal("PRICE_SENSITIVITY", min(confidence, 0.85), evidence=evidence, source="signal_rule", reason="Pricing request indicates that price may be material."))
    if "objection" in names and "price_request" in names:
        out.append(_signal("PRICE_SENSITIVITY", min(confidence, 0.95), evidence=evidence, source="signal_rule", reason="Objection combined with pricing request indicates price sensitivity."))
    if "purchase_interest" in names and "price_request" in names:
        out.append(_signal("PURCHASE_INTENT", confidence, evidence=evidence, source="signal_rule", reason="Purchase interest and pricing request jointly support commercial intent."))

    # De-duplicate by canonical name, keeping the strongest confidence and
    # merging evidence without inventing any new evidence.
    merged: dict[str, dict[str, Any]] = {}
    for item in out:
        current = merged.get(item["name"])
        if current is None or item["confidence"] > current["confidence"]:
            merged[item["name"]] = item
        elif item["confidence"] == current["confidence"]:
            seen = {str(e.get("text")) for e in current["evidence"]}
            current["evidence"].extend(e for e in item["evidence"] if str(e.get("text")) not in seen)

    ordered = list(merged.values())
    ordered.sort(key=lambda x: (-x["confidence"], x["name"]))
    return {
        "signal_set_id": "bs_" + uuid.uuid4().hex[:20],
        "schema_version": SCHEMA_VERSION,
        "understanding_id": understanding.get("understanding_id"),
        "message_id": understanding.get("message_id"),
        "workspace_id": understanding.get("workspace_id"),
        "customer_id": understanding.get("customer_id"),
        "channel": understanding.get("channel"),
        "signals": ordered[:20],
        "signal_names": [x["name"] for x in ordered[:20]],
        "compliance_priority": "OPT_OUT" if "OPT_OUT" in merged else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_signal_set(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported business signal schema")
    if not isinstance(value.get("signals"), list) or not isinstance(value.get("signal_names"), list):
        raise ValueError("signals and signal_names must be arrays")
    for item in value["signals"]:
        if not isinstance(item, dict) or item.get("name") not in SIGNAL_DEFINITIONS:
            raise ValueError("unknown canonical business signal")
        if not 0 <= float(item.get("confidence", -1)) <= 1:
            raise ValueError("signal confidence must be between 0 and 1")
        if not all(isinstance(e, dict) and e.get("verified") for e in item.get("evidence", [])):
            raise ValueError("signal evidence must be verified")
