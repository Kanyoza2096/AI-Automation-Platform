"""Bounded interpretation of inbound customer messages.

This is intentionally deterministic and auditable. It converts customer text into
small business signals; it never sends messages or mutates CRM state.
"""
from __future__ import annotations

import re
from typing import Any

_INTENTS = (
    "purchase_interest",
    "price_request",
    "meeting_request",
    "objection",
    "opt_out",
    "general_inquiry",
)

_PATTERNS = {
    "opt_out": [r"\bstop\b", r"\bunsubscribe\b", r"\bremove me\b", r"\bdo not contact\b"],
    "meeting_request": [r"\b(book|schedule|arrange)\b.{0,30}\b(call|meeting|demo)\b", r"\bcall me\b", r"\bcan we meet\b", r"\bset up a meeting\b"],
    "price_request": [r"\bhow much\b", r"\bwhat(?:'s| is) the (?:price|cost)\b", r"\bpricing\b", r"\bprice\b", r"\bquotation\b", r"\bquote\b"],
    "purchase_interest": [r"\bi(?:'m| am) interested\b", r"\bwant to buy\b", r"\bi want (?:it|this)\b", r"\bhow do i (?:buy|order)\b", r"\bplace (?:an )?order\b", r"\bready to (?:buy|order)\b", r"\bsign me up\b"],
    "objection": [r"\btoo expensive\b", r"\btoo costly\b", r"\bnot interested\b", r"\bnot now\b", r"\bthink about it\b", r"\bneed to think\b"],
}


def interpret_customer_message(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized:
        return {"intent": "general_inquiry", "confidence": 0.0, "signals": [], "interpreted": False}

    matches: list[tuple[str, int]] = []
    for intent, patterns in _PATTERNS.items():
        score = sum(bool(re.search(pattern, normalized, re.I)) for pattern in patterns)
        if score:
            matches.append((intent, score))

    if not matches:
        return {"intent": "general_inquiry", "confidence": 0.35, "signals": ["general_inquiry"], "interpreted": True}

    # Explicit opt-out wins over every other signal.
    matches.sort(key=lambda x: (x[0] == "opt_out", x[1]), reverse=True)
    intent, score = matches[0]
    confidence = min(0.99, 0.72 + (0.08 * max(0, score - 1)))
    signals = [name for name, _ in sorted(matches, key=lambda x: x[1], reverse=True)]
    return {"intent": intent, "confidence": round(confidence, 2), "signals": signals, "interpreted": True}
