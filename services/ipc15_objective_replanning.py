"""IPC-15 — governed objective lifecycle and autonomous replanning.

IPC-14 verifies whether an execution advanced its bound objective. IPC-15 turns
that verified result into a *non-authoritative* lifecycle recommendation. A
recommendation can request a new decision cycle, pause/escalate/recover an
objective, or continue it; it cannot mutate objective state, policy, approval,
or autonomy on its own.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"


class ReplanDisposition(str, Enum):
    CONTINUE = "continue"
    ADAPT = "adapt"
    PAUSE = "pause"
    ESCALATE = "escalate"
    RECOVER = "recover"


class ObjectiveLifecycle(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"


_ALLOWED_TRANSITIONS = {
    ObjectiveLifecycle.ACTIVE.value: {
        ObjectiveLifecycle.ACTIVE.value,
        ObjectiveLifecycle.PAUSED.value,
        ObjectiveLifecycle.BLOCKED.value,
        ObjectiveLifecycle.COMPLETED.value,
    },
    ObjectiveLifecycle.PAUSED.value: {ObjectiveLifecycle.PAUSED.value, ObjectiveLifecycle.ACTIVE.value, ObjectiveLifecycle.BLOCKED.value},
    ObjectiveLifecycle.BLOCKED.value: {ObjectiveLifecycle.BLOCKED.value, ObjectiveLifecycle.ACTIVE.value, ObjectiveLifecycle.PAUSED.value},
    ObjectiveLifecycle.DRAFT.value: {ObjectiveLifecycle.DRAFT.value, ObjectiveLifecycle.ACTIVE.value},
    ObjectiveLifecycle.COMPLETED.value: {ObjectiveLifecycle.COMPLETED.value},
}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def recommend_replan(*, verification: Mapping[str, Any], objective: Mapping[str, Any], now: str | None = None) -> dict[str, Any]:
    """Produce a governed recommendation from verified objective outcome evidence."""
    _validate_input(verification, objective)
    state = str(verification["state"])
    current = str(objective["state"])
    if current != ObjectiveLifecycle.ACTIVE.value:
        disposition = ReplanDisposition.ESCALATE.value if state == "objective_regression" else ReplanDisposition.CONTINUE.value
    elif state == "objective_progress":
        disposition = ReplanDisposition.CONTINUE.value
    elif state == "objective_regression":
        disposition = ReplanDisposition.ADAPT.value
    else:
        disposition = ReplanDisposition.ESCALATE.value

    proposed_state = current
    if disposition == ReplanDisposition.ESCALATE.value and current == ObjectiveLifecycle.ACTIVE.value:
        proposed_state = ObjectiveLifecycle.PAUSED.value
    if disposition == ReplanDisposition.RECOVER.value and current == ObjectiveLifecycle.BLOCKED.value:
        proposed_state = ObjectiveLifecycle.BLOCKED.value

    result = {
        "schema_version": SCHEMA_VERSION,
        "replan_id": "replan_" + _fingerprint({"verification_id": verification["verification_id"], "objective_id": objective["objective_id"]})[:24],
        "workspace_id": objective["workspace_id"],
        "objective_id": objective["objective_id"],
        "objective_version": objective.get("version", 1),
        "source_verification_id": verification["verification_id"],
        "source_outcome_state": state,
        "disposition": disposition,
        "current_state": current,
        "proposed_state": proposed_state,
        "next_cycle": disposition in {ReplanDisposition.CONTINUE.value, ReplanDisposition.ADAPT.value, ReplanDisposition.RECOVER.value},
        "reason": {
            "continue": "Verified evidence indicates progress; continue the governed objective cycle.",
            "adapt": "Verified regression requires a new decision cycle; existing policy remains authoritative.",
            "pause": "Objective is paused pending governed intervention.",
            "escalate": "Evidence is insufficient or the objective is in a non-active lifecycle state; require review.",
            "recover": "Recovery requires a governed recovery decision before a new cycle.",
        }[disposition],
        "observed_at": now,
        "authority": "none",
        "advisory_only": True,
        "policy_effect": "none",
        "approval_effect": "none",
        "execution_effect": "none",
    }
    result["fingerprint"] = _fingerprint(result)
    validate_replan_recommendation(result)
    return result


def validate_replan_recommendation(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported replanning schema")
    for key in ("replan_id", "workspace_id", "objective_id", "source_verification_id", "disposition", "current_state", "proposed_state"):
        if not value.get(key):
            raise ValueError(f"{key} is required")
    if value["disposition"] not in {d.value for d in ReplanDisposition}:
        raise ValueError("invalid replanning disposition")
    if value["current_state"] not in {s.value for s in ObjectiveLifecycle} or value["proposed_state"] not in {s.value for s in ObjectiveLifecycle}:
        raise ValueError("invalid objective lifecycle state")
    if value["proposed_state"] not in _ALLOWED_TRANSITIONS[value["current_state"]]:
        raise ValueError("invalid objective lifecycle transition")
    fingerprint = value.get("fingerprint")
    if not fingerprint:
        raise ValueError("replanning fingerprint is required")
    payload = dict(value)
    payload.pop("fingerprint", None)
    if _fingerprint(payload) != fingerprint:
        raise ValueError("replanning fingerprint mismatch")
    if value.get("authority") != "none" or value.get("advisory_only") is not True:
        raise ValueError("replanning recommendation cannot grant authority")
    for key in ("policy_effect", "approval_effect", "execution_effect"):
        if value.get(key) != "none":
            raise ValueError("replanning cannot mutate governance or execution")


def admit_objective_lifecycle_transition(*, objective: Mapping[str, Any], recommendation: Mapping[str, Any], governed_decision: Mapping[str, Any]) -> dict[str, Any]:
    """Admit a lifecycle transition only when a separate governance decision authorizes it."""
    validate_replan_recommendation(recommendation)
    if str(objective.get("workspace_id")) != str(recommendation.get("workspace_id")) or str(objective.get("objective_id")) != str(recommendation.get("objective_id")):
        raise ValueError("objective/replan tenant or identity mismatch")
    if str(objective.get("state")) != str(recommendation.get("current_state")):
        raise ValueError("objective state changed since recommendation")
    if governed_decision.get("authority") == "none" or governed_decision.get("decision") not in {"allow", "approve"}:
        raise ValueError("governed lifecycle decision required")
    if governed_decision.get("workspace_id") != objective.get("workspace_id") or governed_decision.get("objective_id") != objective.get("objective_id"):
        raise ValueError("governed lifecycle decision binding mismatch")
    return {
        "transition_id": "transition_" + _fingerprint({"replan_id": recommendation["replan_id"], "decision_id": governed_decision.get("decision_id")})[:24],
        "workspace_id": objective["workspace_id"],
        "objective_id": objective["objective_id"],
        "from_state": objective["state"],
        "to_state": recommendation["proposed_state"],
        "replan_id": recommendation["replan_id"],
        "decision_id": governed_decision["decision_id"],
        "authority": governed_decision["authority"],
        "execution_effect": "none",
    }


def _validate_input(verification: Mapping[str, Any], objective: Mapping[str, Any]) -> None:
    if verification.get("authority") != "none":
        raise ValueError("verification cannot grant replanning authority")
    if verification.get("workspace_id") != objective.get("workspace_id"):
        raise ValueError("objective/replan workspace mismatch")
    if verification.get("objective_id") != objective.get("objective_id"):
        raise ValueError("objective/replan binding mismatch")
    if verification.get("state") not in {"objective_progress", "objective_regression", "objective_uncertain"}:
        raise ValueError("unsupported objective outcome state")
    if objective.get("state") not in {s.value for s in ObjectiveLifecycle}:
        raise ValueError("invalid objective lifecycle state")
