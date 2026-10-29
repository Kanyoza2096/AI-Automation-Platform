"""Phase 5.4 — bounded learned context for future intelligence decisions.

This module is deliberately a projection, not a decision engine.  It reads
append-only learning feedback and exposes a bounded advisory context that a
future Decision Engine may consume as evidence.  It cannot alter policy,
authorization, autonomy, approval, ActionIntent, Action Kernel, provider state,
or historical outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

SCHEMA = "KDO.IntelligenceLearningDecisionContext/v1"
MAX_ITEMS = 10
ALLOWED_STATES = {"positive", "negative", "uncertain", "insufficient_evidence"}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class LearningDecisionContext:
    workspace_id: str
    context_id: str
    feedback: tuple[dict[str, Any], ...]
    source_fingerprints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        material = {
            "schema_version": SCHEMA,
            "workspace_id": self.workspace_id,
            "context_id": self.context_id,
            "feedback": list(self.feedback),
            "source_fingerprints": list(self.source_fingerprints),
        }
        return {
            **material,
            "advisory_only": True,
            "authority": "none",
            "policy_effect": "none",
            "approval_effect": "none",
            "autonomy_effect": "none",
            "execution_effect": "none",
            "action_override": None,
            "policy_override": None,
            "approval_override": None,
            "historical_mutation": False,
            "causal_claim": False,
        }


def build_learning_decision_context(
    *,
    workspace_id: str,
    learning_feedback: Iterable[Mapping[str, Any]],
    max_items: int = MAX_ITEMS,
) -> LearningDecisionContext:
    """Project trusted learning feedback into bounded advisory decision context."""
    ws = str(workspace_id or "").strip()
    if not ws:
        raise ValueError("workspace_id is required")
    if not 1 <= int(max_items) <= MAX_ITEMS:
        raise ValueError(f"max_items must be between 1 and {MAX_ITEMS}")

    selected: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    seen: set[str] = set()

    for raw in learning_feedback:
        if not isinstance(raw, Mapping):
            raise ValueError("learning feedback rows must be mappings")
        validate_learning_feedback_projection(raw, workspace_id=ws)
        fp = str(raw["evidence_fingerprint"])
        if fp in seen:
            continue
        seen.add(fp)
        selected.append({
            "workspace_id": ws,
            "feedback_id": str(raw["feedback_id"]),
            "execution_id": str(raw["execution_id"]),
            "outcome_id": str(raw["outcome_id"]),
            "learning_state": str(raw["learning_state"]),
            "confidence": float(raw["confidence"]),
            "evidence_fingerprint": fp,
            "authority": "none",
            "causal_claim": False,
            "governance_mutation": False,
            "execution_authority": False,
        })
        fingerprints.append(fp)
        if len(selected) >= int(max_items):
            break

    material = {
        "schema_version": SCHEMA,
        "workspace_id": ws,
        "feedback": selected,
        "source_fingerprints": fingerprints,
    }
    context_id = "ildc_" + _fingerprint(material)[:24]
    return LearningDecisionContext(
        workspace_id=ws,
        context_id=context_id,
        feedback=tuple(selected),
        source_fingerprints=tuple(fingerprints),
    )


def validate_learning_feedback_projection(
    value: Mapping[str, Any], *, workspace_id: str
) -> None:
    """Validate that only Phase 5.3 learning evidence enters the decision plane."""
    if str(value.get("workspace_id") or "") != workspace_id:
        raise PermissionError("learning feedback workspace mismatch")
    for key in (
        "feedback_id", "execution_id", "outcome_id", "evidence_fingerprint",
    ):
        if not str(value.get(key) or "").strip():
            raise ValueError(f"learning feedback {key} is required")
    if value.get("learning_state") not in ALLOWED_STATES:
        raise ValueError("invalid learning_state")
    confidence = float(value.get("confidence", -1))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("invalid learning confidence")
    if value.get("authority") != "none":
        raise ValueError("learning feedback cannot grant authority")
    if value.get("causal_claim") is not False:
        raise ValueError("learning feedback cannot claim causation")
    if value.get("governance_mutation") is not False:
        raise ValueError("learning feedback cannot mutate governance")
    if value.get("execution_authority") is not False:
        raise ValueError("learning feedback cannot grant execution authority")


def validate_learning_decision_context(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA:
        raise ValueError("unsupported learning decision context schema")
    if not str(value.get("workspace_id") or "").strip():
        raise ValueError("workspace_id is required")
    feedback = value.get("feedback")
    if not isinstance(feedback, list) or len(feedback) > MAX_ITEMS:
        raise ValueError("feedback must be a bounded array")
    if value.get("advisory_only") is not True:
        raise ValueError("learning context must be advisory only")
    for key in ("authority", "policy_effect", "approval_effect", "autonomy_effect", "execution_effect"):
        if value.get(key) != "none":
            raise ValueError(f"{key} cannot be changed by learning")
    for key in ("action_override", "policy_override", "approval_override"):
        if value.get(key) is not None:
            raise ValueError(f"{key} cannot be supplied by learning")
    if value.get("historical_mutation") is not False:
        raise ValueError("learning context cannot mutate history")
    if value.get("causal_claim") is not False:
        raise ValueError("learning context cannot make a causal claim")
    fps = value.get("source_fingerprints")
    if not isinstance(fps, list) or len(fps) != len(feedback):
        raise ValueError("source_fingerprints mismatch")
    for item in feedback:
        if not isinstance(item, Mapping):
            raise ValueError("invalid learning context item")
        validate_learning_feedback_projection(item, workspace_id=str(value["workspace_id"]))


__all__ = [
    "SCHEMA",
    "MAX_ITEMS",
    "LearningDecisionContext",
    "build_learning_decision_context",
    "validate_learning_feedback_projection",
    "validate_learning_decision_context",
]
