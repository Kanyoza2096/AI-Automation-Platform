"""V66.5 — governed learning projection into the decision plane.

Promoted learning may inform future reasoning, but it is advisory context only.
It never grants authority, changes policy, bypasses approvals, or executes an action.
"""
from __future__ import annotations

from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
ALLOWED_STATES = {"promoted"}
ALLOWED_TARGETS = {"action", "decision", "workflow", "customer_outcome"}
MAX_ITEMS = 10


def _validate_decision_input(value: dict[str, Any]) -> None:
    from services.business_understanding_decision_input import validate_decision_input
    validate_decision_input(value)


def _validate_learning(value: dict[str, Any]) -> None:
    from services.business_learning_governance import validate_governed_learning
    validate_governed_learning(value)
    if value.get("promotion_state") not in ALLOWED_STATES:
        raise ValueError("only promoted learning may enter decision context")
    if value.get("authority") != "none":
        raise ValueError("learning context cannot grant authority")


def build_learning_decision_context(
    decision_input: dict[str, Any],
    governed_learning: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Project only trusted, promoted learning as bounded advisory context."""
    _validate_decision_input(decision_input)
    selected: list[dict[str, Any]] = []
    workspace_id = decision_input["workspace_id"]

    for knowledge in governed_learning:
        _validate_learning(knowledge)
        if knowledge["workspace_id"] != workspace_id:
            raise ValueError("learning and decision input workspace_id must match")
        if knowledge["target_type"] not in ALLOWED_TARGETS:
            raise ValueError("unsupported learning target type")
        selected.append({
            "knowledge_id": knowledge["knowledge_id"],
            "target_type": knowledge["target_type"],
            "target_id": knowledge["target_id"],
            "direction": knowledge["direction"],
            "confidence": float(knowledge["confidence"]),
            "support_count": int(knowledge["support_count"]),
            "consistency": float(knowledge["consistency"]),
            "lesson": knowledge["lesson"],
        })
        if len(selected) >= MAX_ITEMS:
            break

    return {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "understanding_id": decision_input["understanding_id"],
        "knowledge": selected,
        "knowledge_count": len(selected),
        "advisory_only": True,
        "authority": "none",
        "policy_effect": "none",
        "approval_effect": "none",
        "execution_effect": "none",
    }


def validate_learning_decision_context(value: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported learning decision context schema")
    if not value.get("workspace_id") or not value.get("understanding_id"):
        raise ValueError("workspace_id and understanding_id are required")
    knowledge = value.get("knowledge")
    if not isinstance(knowledge, list) or len(knowledge) > MAX_ITEMS:
        raise ValueError("knowledge must be a bounded array")
    if value.get("knowledge_count") != len(knowledge):
        raise ValueError("knowledge_count mismatch")
    if value.get("advisory_only") is not True:
        raise ValueError("learning context must be advisory only")
    for key in ("authority", "policy_effect", "approval_effect", "execution_effect"):
        if value.get(key) != ("none" if key == "authority" else "none"):
            raise ValueError(f"{key} cannot be changed by learning")
    for item in knowledge:
        if not isinstance(item, dict) or not item.get("knowledge_id"):
            raise ValueError("invalid learning context item")
        if item.get("direction") not in {"positive", "negative", "mixed"}:
            raise ValueError("invalid learning direction")
        if not 0 <= float(item.get("confidence", -1)) <= 1:
            raise ValueError("invalid learning confidence")
