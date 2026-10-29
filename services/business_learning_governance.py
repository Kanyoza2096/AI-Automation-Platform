"""V66.4 — governance for promoting learned business signals.

Learning is not trusted knowledge merely because it was observed. This module
keeps raw learning signals separate from reusable knowledge and applies
explicit promotion, contradiction, and staleness rules. It never grants
execution authority or mutates policy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "1.0"
PROMOTION_STATES = {"candidate", "promoted", "rejected", "stale", "contradicted", "review_required"}
LEARNING_STATES = {"positive", "negative", "uncertain", "insufficient_evidence"}
TARGET_TYPES = {"action", "decision", "workflow", "customer_outcome"}

MIN_CONFIDENCE = 0.75
MIN_SUPPORT = 3
MAX_AGE_DAYS = 30
MIN_CONSISTENCY = 0.67


@dataclass(frozen=True)
class GovernedLearning:
    knowledge_id: str
    workspace_id: str
    target_type: str
    target_id: str
    promotion_state: str
    direction: str
    confidence: float
    support_count: int
    consistency: float
    evidence: dict[str, Any]
    lesson: str
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "knowledge_id": self.knowledge_id,
            "workspace_id": self.workspace_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "promotion_state": self.promotion_state,
            "direction": self.direction,
            "confidence": self.confidence,
            "support_count": self.support_count,
            "consistency": self.consistency,
            "evidence": self.evidence,
            "lesson": self.lesson,
            "authority": self.authority,
        }


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _validate_signal(signal: dict[str, Any]) -> None:
    if not isinstance(signal, dict):
        raise ValueError("learning signal must be an object")
    if signal.get("schema_version") != "1.0":
        raise ValueError("unsupported learning signal schema")
    for key in ("signal_id", "workspace_id", "target_type", "target_id", "learning_state"):
        if not signal.get(key):
            raise ValueError(f"{key} is required")
    if signal["target_type"] not in TARGET_TYPES:
        raise ValueError("invalid target_type")
    if signal["learning_state"] not in LEARNING_STATES:
        raise ValueError("invalid learning_state")
    if signal.get("authority") != "none":
        raise ValueError("learning governance cannot grant authority")


def _age_days(signal: dict[str, Any], now: datetime) -> Optional[float]:
    created = _parse_time(signal.get("created_at")) or _parse_time(signal.get("observed_at"))
    if created is None:
        return None
    return max(0.0, (now - created).total_seconds() / 86400.0)


def evaluate_learning_governance(
    signals: Iterable[dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    max_age_days: int = MAX_AGE_DAYS,
) -> list[dict[str, Any]]:
    """Evaluate a set of signals without changing their underlying truth.

    Promotion requires repeated support, adequate confidence, consistency, and
    freshness. Conflicting positive/negative evidence is explicitly surfaced.
    """
    now = now or datetime.now(timezone.utc)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for signal in signals:
        _validate_signal(signal)
        if signal.get("learning_state") in {"uncertain", "insufficient_evidence"}:
            continue
        key = (signal["workspace_id"], signal["target_type"], signal["target_id"])
        grouped.setdefault(key, []).append(signal)

    results: list[dict[str, Any]] = []
    for (workspace_id, target_type, target_id), group in grouped.items():
        positive = sum(s["learning_state"] == "positive" for s in group)
        negative = sum(s["learning_state"] == "negative" for s in group)
        support = positive + negative
        direction = "positive" if positive > negative else "negative" if negative > positive else "mixed"
        consistency = (max(positive, negative) / support) if support else 0.0
        confidences = [float(s.get("confidence", 0.0)) for s in group]
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        ages = [a for s in group if (a := _age_days(s, now)) is not None]
        stale = bool(ages) and max(ages) > max_age_days

        if direction == "mixed" or consistency < MIN_CONSISTENCY:
            state = "contradicted"
        elif stale:
            state = "stale"
        elif support >= MIN_SUPPORT and confidence >= MIN_CONFIDENCE and consistency >= MIN_CONSISTENCY:
            state = "promoted"
        else:
            state = "candidate"

        material = {
            "workspace_id": workspace_id,
            "target_type": target_type,
            "target_id": target_id,
            "direction": direction,
            "support_count": support,
            "state": state,
        }
        results.append({
            "schema_version": SCHEMA_VERSION,
            "knowledge_id": "know_" + _fingerprint(material)[:24],
            "workspace_id": workspace_id,
            "target_type": target_type,
            "target_id": target_id,
            "promotion_state": state,
            "direction": direction,
            "confidence": max(0.0, min(1.0, confidence)),
            "support_count": support,
            "consistency": round(consistency, 6),
            "evidence": {
                "source_signal_ids": [s["signal_id"] for s in group],
                "positive_count": positive,
                "negative_count": negative,
                "max_age_days": max(ages) if ages else None,
            },
            "lesson": (
                "Repeated measured outcomes support this reusable pattern; it remains advisory only."
                if state == "promoted" else
                "Learning remains a candidate and requires more consistent evidence before promotion."
                if state == "candidate" else
                "Conflicting measured outcomes prevent promotion; keep the pattern under review."
                if state == "contradicted" else
                "Evidence is stale; do not use it as current reusable knowledge."
            ),
            "authority": "none",
        })
    for result in results:
        validate_governed_learning(result)
    return results


def validate_governed_learning(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported governed learning schema")
    for key in ("knowledge_id", "workspace_id", "target_type", "target_id", "promotion_state", "direction"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["target_type"] not in TARGET_TYPES:
        raise ValueError("invalid target_type")
    if value["promotion_state"] not in PROMOTION_STATES:
        raise ValueError("invalid promotion_state")
    if value["direction"] not in {"positive", "negative", "mixed"}:
        raise ValueError("invalid direction")
    if not 0 <= float(value.get("confidence", -1)) <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if int(value.get("support_count", -1)) < 0:
        raise ValueError("support_count must be non-negative")
    if not 0 <= float(value.get("consistency", -1)) <= 1:
        raise ValueError("consistency must be between 0 and 1")
    if value.get("authority") != "none":
        raise ValueError("governed learning cannot grant authority")
    if not isinstance(value.get("evidence", {}), dict):
        raise ValueError("evidence must be an object")


def decay_learning(value: dict[str, Any], *, age_days: float, half_life_days: float = 30.0) -> dict[str, Any]:
    """Return a decayed confidence view; never mutates the source record."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    validate_governed_learning(value)
    import math
    factor = 0.5 ** (max(0.0, age_days) / half_life_days)
    result = dict(value)
    result["confidence"] = round(float(value["confidence"]) * factor, 6)
    result["evidence"] = dict(value.get("evidence", {}), decay_factor=round(factor, 6), age_days=age_days)
    result["authority"] = "none"
    validate_governed_learning(result)
    return result
