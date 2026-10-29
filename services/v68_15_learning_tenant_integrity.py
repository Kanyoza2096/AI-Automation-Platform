"""V68.15 — tenant-bound learning integrity.

Learning is advisory knowledge, but it can influence future decisions. This
module makes the evidence chain explicit: governed knowledge, source learning
signals, and projected decision context must all belong to the same workspace.
It never grants authority or executes actions.
"""
from __future__ import annotations

from typing import Any, Iterable


def assert_learning_signal_workspace(learning: dict[str, Any], signals: Iterable[dict[str, Any]]) -> None:
    """Fail closed if governed learning cites a signal from another tenant."""
    if not isinstance(learning, dict):
        raise ValueError("learning must be an object")
    workspace_id = str(learning.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("learning workspace_id is required")
    evidence = learning.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("learning evidence must be an object")
    source_ids = evidence.get("source_signal_ids", [])
    if not isinstance(source_ids, list):
        raise ValueError("source_signal_ids must be an array")
    by_id = {}
    for signal in signals:
        if not isinstance(signal, dict) or not signal.get("signal_id"):
            continue
        by_id[str(signal["signal_id"])] = signal
    for signal_id in source_ids:
        signal = by_id.get(str(signal_id))
        if signal is None:
            raise ValueError("learning references an unknown source signal")
        if str(signal.get("workspace_id") or "").strip() != workspace_id:
            raise ValueError("learning source signal workspace mismatch")


def assert_decision_context_learning_workspace(
    decision_input: dict[str, Any], learning: Iterable[dict[str, Any]],
) -> None:
    """Validate the complete learning set before it reaches decision context."""
    if not isinstance(decision_input, dict):
        raise ValueError("decision input must be an object")
    workspace_id = str(decision_input.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("decision input workspace_id is required")
    for item in learning:
        if not isinstance(item, dict):
            raise ValueError("learning item must be an object")
        if str(item.get("workspace_id") or "").strip() != workspace_id:
            raise ValueError("decision context learning workspace mismatch")
        if item.get("authority") != "none":
            raise ValueError("learning cannot grant authority")


def assert_learning_context_integrity(context: dict[str, Any], governed_learning: Iterable[dict[str, Any]]) -> None:
    """Ensure projected knowledge IDs are owned by the same tenant."""
    if not isinstance(context, dict):
        raise ValueError("learning decision context must be an object")
    workspace_id = str(context.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("context workspace_id is required")
    known = {}
    for item in governed_learning:
        if isinstance(item, dict) and item.get("knowledge_id"):
            known[str(item["knowledge_id"])] = item
    for projected in context.get("knowledge", []):
        kid = str(projected.get("knowledge_id") or "")
        source = known.get(kid)
        if source is None:
            raise ValueError("decision context references unknown knowledge")
        if str(source.get("workspace_id") or "").strip() != workspace_id:
            raise ValueError("decision context knowledge workspace mismatch")
