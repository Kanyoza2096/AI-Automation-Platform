"""V66.3 — bounded learning from measured business outcomes.

This module converts measured, attributed outcomes into durable learning
signals. Learning may improve memory and recommendations, but it is never an
authorization source and never mutates policy, approvals, action contracts, or
execution permissions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "1.0"
LEARNING_STATES = {"positive", "negative", "uncertain", "insufficient_evidence"}
LEARNING_TARGETS = {"action", "decision", "workflow", "customer_outcome"}


@dataclass(frozen=True)
class LearningSignal:
    signal_id: str
    workspace_id: str
    execution_id: Optional[str]
    observation_id: Optional[str]
    attribution_id: Optional[str]
    target_type: str
    target_id: str
    learning_state: str
    confidence: float
    evidence: dict[str, Any]
    lesson: str
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "signal_id": self.signal_id,
            "workspace_id": self.workspace_id,
            "execution_id": self.execution_id,
            "observation_id": self.observation_id,
            "attribution_id": self.attribution_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "learning_state": self.learning_state,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "lesson": self.lesson,
            "authority": self.authority,
        }


def _fingerprint(material: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _validate_input(observation: dict[str, Any], attribution: Optional[dict[str, Any]]) -> None:
    if not isinstance(observation, dict):
        raise ValueError("observation must be an object")
    if observation.get("observation_type") != "business_outcome":
        raise ValueError("V66.3 requires a business outcome observation")
    if not observation.get("workspace_id") or not observation.get("observation_id"):
        raise ValueError("workspace_id and observation_id are required")
    if attribution is not None:
        if attribution.get("workspace_id") != observation.get("workspace_id"):
            raise ValueError("observation and attribution workspace_id must match")
        if attribution.get("attribution_status") != "attributed":
            raise ValueError("learning requires an attributed outcome")
        if not attribution.get("execution_id"):
            raise ValueError("attributed learning requires execution_id")


def build_learning_signal(
    observation: dict[str, Any],
    attribution: Optional[dict[str, Any]] = None,
    *,
    target_type: str = "action",
    target_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create one conservative learning signal from measured outcome truth."""
    _validate_input(observation, attribution)
    if target_type not in LEARNING_TARGETS:
        raise ValueError("invalid learning target_type")

    business_success = observation.get("business_success")
    confidence = observation.get("business_success_confidence")
    confidence_value = float(confidence) if confidence is not None else 0.0
    execution_id = (attribution or {}).get("execution_id") or observation.get("execution_id")

    # Unattributed/ambiguous outcomes must never become action-level learning.
    if attribution is not None and attribution.get("attribution_status") != "attributed":
        state = "insufficient_evidence"
    elif business_success is None:
        state = "uncertain"
    elif confidence is None or confidence_value < 0.5:
        state = "uncertain"
    else:
        state = "positive" if business_success is True else "negative"

    if target_id is None:
        target_id = observation.get("action") or (attribution or {}).get("action") or execution_id
    if not target_id:
        raise ValueError("target_id is required when action/execution identity is unavailable")

    lesson_map = {
        "positive": "Measured business outcome supports this governed path; retain as evidence, not authority.",
        "negative": "Measured business outcome did not succeed; retain the evidence for future planning and diagnosis.",
        "uncertain": "Business outcome evidence is insufficiently certain; do not treat this as a successful learned path.",
        "insufficient_evidence": "Outcome attribution is insufficient; do not learn an action-level causal relationship.",
    }
    material = {
        "workspace_id": observation["workspace_id"],
        "observation_id": observation["observation_id"],
        "attribution_id": (attribution or {}).get("attribution_id"),
        "target_type": target_type,
        "target_id": target_id,
        "learning_state": state,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "signal_id": "learn_" + _fingerprint(material)[:24],
        "workspace_id": observation["workspace_id"],
        "execution_id": execution_id,
        "observation_id": observation["observation_id"],
        "attribution_id": (attribution or {}).get("attribution_id"),
        "target_type": target_type,
        "target_id": target_id,
        "learning_state": state,
        "confidence": max(0.0, min(1.0, confidence_value)),
        "evidence": {
            "outcome_state": observation.get("outcome_state"),
            "business_success": business_success,
            "source": observation.get("source"),
            "attribution_method": (attribution or {}).get("attribution_method"),
            "attribution_confidence": (attribution or {}).get("confidence"),
        },
        "lesson": lesson_map[state],
        "authority": "none",
    }
    validate_learning_signal(result)
    return result


def build_learning_batch(
    observations: Iterable[dict[str, Any]],
    attributions_by_observation: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Build only independently attributable learning signals."""
    attributions_by_observation = attributions_by_observation or {}
    results: list[dict[str, Any]] = []
    for observation in observations:
        attribution = attributions_by_observation.get(observation.get("observation_id"))
        try:
            results.append(build_learning_signal(observation, attribution))
        except ValueError:
            # Learning is fail-closed per observation; one malformed/unattributed
            # record must not poison the rest of the batch.
            continue
    return results


def validate_learning_signal(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported learning signal schema")
    for key in ("signal_id", "workspace_id", "target_type", "target_id", "learning_state", "lesson"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["target_type"] not in LEARNING_TARGETS:
        raise ValueError("invalid target_type")
    if value["learning_state"] not in LEARNING_STATES:
        raise ValueError("invalid learning_state")
    if not 0.0 <= float(value.get("confidence", -1)) <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if value.get("authority") != "none":
        raise ValueError("learning signals cannot grant authority")
    if not isinstance(value.get("evidence", {}), dict):
        raise ValueError("evidence must be an object")
