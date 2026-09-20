"""V66.6 — learning feedback and effectiveness evaluation.

This module evaluates whether *using* promoted learning is associated with
better measured business outcomes. It deliberately does not claim causality
from correlation alone and never mutates policy, authority, approvals, or
execution permissions.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "1.0"
EVALUATION_STATES = {
    "effective",
    "ineffective",
    "mixed",
    "insufficient_evidence",
    "review_required",
}

MIN_EVALUATION_SAMPLES = 3
MIN_CONFIDENCE = 0.70
MIN_LIFT = 0.10
MAX_CONTRADICTION_RATE = 0.33


@dataclass(frozen=True)
class LearningEffectiveness:
    evaluation_id: str
    workspace_id: str
    knowledge_id: str
    evaluation_state: str
    sample_count: int
    success_count: int
    failure_count: int
    uncertain_count: int
    success_rate: float
    baseline_success_rate: Optional[float]
    lift: Optional[float]
    confidence: float
    causal_claim: str
    evidence: dict[str, Any]
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "workspace_id": self.workspace_id,
            "knowledge_id": self.knowledge_id,
            "evaluation_state": self.evaluation_state,
            "sample_count": self.sample_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "uncertain_count": self.uncertain_count,
            "success_rate": self.success_rate,
            "baseline_success_rate": self.baseline_success_rate,
            "lift": self.lift,
            "confidence": self.confidence,
            "causal_claim": self.causal_claim,
            "evidence": self.evidence,
            "authority": self.authority,
        }


def _fingerprint(material: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _validate_knowledge(knowledge: dict[str, Any]) -> None:
    from services.business_learning_governance import validate_governed_learning
    validate_governed_learning(knowledge)
    if knowledge.get("promotion_state") != "promoted":
        raise ValueError("effectiveness evaluation requires promoted learning")
    if knowledge.get("authority") != "none":
        raise ValueError("learning cannot grant authority")


def _validate_feedback(feedback: dict[str, Any], workspace_id: str, knowledge_id: str) -> None:
    if not isinstance(feedback, dict):
        raise ValueError("feedback must be an object")
    if feedback.get("workspace_id") != workspace_id:
        raise ValueError("feedback workspace mismatch")
    if feedback.get("knowledge_id") != knowledge_id:
        raise ValueError("feedback knowledge mismatch")
    if not feedback.get("outcome_observation_id"):
        raise ValueError("outcome_observation_id is required")
    state = feedback.get("outcome_state")
    if state not in {"succeeded", "failed", "uncertain", "observed", "unknown"}:
        raise ValueError("invalid outcome_state")
    success = feedback.get("business_success")
    if state == "unknown" and success is not None:
        raise ValueError("unknown outcome cannot claim business success")
    if success is not None and not isinstance(success, bool):
        raise ValueError("business_success must be boolean or null")
    confidence = feedback.get("confidence")
    if confidence is not None and not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("invalid feedback confidence")
    if feedback.get("authority", "none") != "none":
        raise ValueError("feedback cannot grant authority")


def build_learning_effectiveness(
    knowledge: dict[str, Any],
    feedback: Iterable[dict[str, Any]],
    *,
    baseline_success_rate: Optional[float] = None,
    baseline_sample_count: int = 0,
) -> dict[str, Any]:
    """Evaluate observed outcomes after a promoted learning context was used.

    Feedback records must explicitly identify the knowledge item used. This
    prevents accidental attribution merely because an outcome happened near a
    decision that happened to have similar intent.

    ``baseline_success_rate`` is an optional comparison cohort. Even with a
    positive lift, the result is described as association rather than causation.
    """
    _validate_knowledge(knowledge)
    workspace_id = knowledge["workspace_id"]
    knowledge_id = knowledge["knowledge_id"]
    rows = list(feedback)
    for row in rows:
        _validate_feedback(row, workspace_id, knowledge_id)

    if baseline_success_rate is not None and not 0.0 <= float(baseline_success_rate) <= 1.0:
        raise ValueError("baseline_success_rate must be between 0 and 1")
    if baseline_sample_count < 0:
        raise ValueError("baseline_sample_count must be non-negative")

    # Only explicit business success/failure counts as an effectiveness sample.
    success = sum(row.get("business_success") is True for row in rows)
    failure = sum(row.get("business_success") is False for row in rows)
    uncertain = len(rows) - success - failure
    sample_count = success + failure
    success_rate = success / sample_count if sample_count else 0.0

    lift = None
    if baseline_success_rate is not None and sample_count:
        lift = round(success_rate - float(baseline_success_rate), 6)

    # A simple bounded confidence score based on sample sufficiency, outcome
    # certainty and promoted-knowledge confidence. This is not statistical
    # significance and is intentionally labelled as such.
    sample_factor = min(1.0, sample_count / MIN_EVALUATION_SAMPLES)
    certainty_factor = sample_count / len(rows) if rows else 0.0
    confidence = round(
        min(1.0, 0.45 * sample_factor + 0.30 * certainty_factor + 0.25 * float(knowledge["confidence"])),
        6,
    )

    contradiction_rate = min(success, failure) / sample_count if sample_count else 0.0
    if sample_count < MIN_EVALUATION_SAMPLES or confidence < MIN_CONFIDENCE:
        state = "insufficient_evidence"
    elif contradiction_rate > MAX_CONTRADICTION_RATE:
        state = "mixed"
    elif lift is not None and lift >= MIN_LIFT and baseline_sample_count >= MIN_EVALUATION_SAMPLES:
        state = "effective"
    elif lift is not None and lift < 0:
        state = "ineffective"
    elif success_rate >= 0.5 and lift is None:
        # Without a baseline we can describe the observed result, but not claim
        # improvement attributable to learning.
        state = "review_required"
    else:
        state = "mixed"

    material = {
        "workspace_id": workspace_id,
        "knowledge_id": knowledge_id,
        "outcome_observation_ids": sorted(r["outcome_observation_id"] for r in rows),
        "baseline_success_rate": baseline_success_rate,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": "leval_" + _fingerprint(material)[:24],
        "workspace_id": workspace_id,
        "knowledge_id": knowledge_id,
        "evaluation_state": state,
        "sample_count": sample_count,
        "success_count": success,
        "failure_count": failure,
        "uncertain_count": uncertain,
        "success_rate": round(success_rate, 6),
        "baseline_success_rate": baseline_success_rate,
        "baseline_sample_count": baseline_sample_count,
        "lift": lift,
        "confidence": confidence,
        "causal_claim": "association_only",
        "evidence": {
            "outcome_observation_ids": [r["outcome_observation_id"] for r in rows],
            "knowledge_confidence": knowledge["confidence"],
            "contradiction_rate": round(contradiction_rate, 6),
            "sample_factor": round(sample_factor, 6),
            "certainty_factor": round(certainty_factor, 6),
        },
        "authority": "none",
    }
    validate_learning_effectiveness(result)
    return result


def validate_learning_effectiveness(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported learning effectiveness schema")
    for key in ("evaluation_id", "workspace_id", "knowledge_id", "evaluation_state"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["evaluation_state"] not in EVALUATION_STATES:
        raise ValueError("invalid evaluation_state")
    for key in ("sample_count", "success_count", "failure_count", "uncertain_count"):
        if int(value.get(key, -1)) < 0:
            raise ValueError(f"{key} must be non-negative")
    if int(value["sample_count"]) != int(value["success_count"]) + int(value["failure_count"]):
        raise ValueError("sample_count must equal success_count + failure_count")
    for key in ("success_rate", "confidence"):
        if not 0.0 <= float(value.get(key, -1)) <= 1.0:
            raise ValueError(f"invalid {key}")
    baseline = value.get("baseline_success_rate")
    if baseline is not None and not 0.0 <= float(baseline) <= 1.0:
        raise ValueError("invalid baseline_success_rate")
    if value.get("lift") is not None and not -1.0 <= float(value["lift"]) <= 1.0:
        raise ValueError("invalid lift")
    if value.get("causal_claim") != "association_only":
        raise ValueError("effectiveness evaluation cannot claim causality")
    if value.get("authority") != "none":
        raise ValueError("effectiveness evaluation cannot grant authority")
    if not isinstance(value.get("evidence", {}), dict):
        raise ValueError("evidence must be an object")


def build_learning_feedback_batch(
    knowledge: dict[str, Any],
    feedback: Iterable[dict[str, Any]],
    *,
    baseline_success_rate: Optional[float] = None,
    baseline_sample_count: int = 0,
) -> dict[str, Any]:
    """Named batch entry point for future worker/analytics integration."""
    return build_learning_effectiveness(
        knowledge,
        feedback,
        baseline_success_rate=baseline_success_rate,
        baseline_sample_count=baseline_sample_count,
    )
