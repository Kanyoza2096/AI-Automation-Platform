"""R13-H runtime admission boundary.

Hardens the handoff from the existing Knowledge Plane into the existing Agent
Runtime. This module is deliberately an admission/context planner, not a new
runtime, knowledge store, authorization layer, or execution engine.
"""
from __future__ import annotations

from typing import Any, Iterable


BLOCKED_STATES = {
    "MISSING",
    "UNCONFIRMED",
    "CONFLICT",
    "CONFIRMED_UNSUPPORTED",
    "INVALID_REQUIREMENT",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _fact_key(fact: dict[str, Any]) -> str:
    return _norm(
        fact.get("knowledge_key")
        or fact.get("requirement")
        or fact.get("key")
        or fact.get("subject")
    )


def _active(fact: dict[str, Any]) -> bool:
    status = _norm(fact.get("status") or "confirmed")
    return status not in {"superseded", "revoked", "archived"} and not bool(fact.get("superseded"))


def _confirmed(fact: dict[str, Any]) -> bool:
    return _norm(fact.get("status") or "confirmed") == "confirmed" and _active(fact)


def _citation(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": fact.get("id") or fact.get("fact_id"),
        "knowledge_key": _fact_key(fact),
        "source": fact.get("source"),
        "source_span": fact.get("source_span"),
        "extraction_id": fact.get("extraction_id"),
        "confirmed_at": fact.get("confirmed_at"),
    }


def _requirement_state(evaluation: dict[str, Any], key: str) -> str | None:
    wanted = _norm(key)
    for item in evaluation.get("requirements", []):
        if _norm(item.get("knowledge_key")) == wanted:
            return item.get("state")
    return None


def prepare_runtime_admission(
    *,
    workspace_id: str,
    question: str,
    knowledge_keys: Iterable[str],
    evaluation: dict[str, Any],
    facts: list[dict[str, Any]],
    require_fresh: bool = False,
) -> dict[str, Any]:
    """Prepare governed runtime context without invoking the Agent Runtime.

    Only confirmed/current facts belonging to READY requirements are admitted.
    Conflicts, unsupported confirmation, missing knowledge, and unconfirmed
    knowledge produce a refusal/handoff boundary instead of best-effort context.
    """
    keys = [_norm(k) for k in knowledge_keys if _norm(k)]
    blocked: list[dict[str, Any]] = []
    admitted_keys: list[str] = []

    for key in keys:
        state = _requirement_state(evaluation, key)
        if state in BLOCKED_STATES:
            blocked.append({"knowledge_key": key, "state": state})
        elif state == "READY":
            admitted_keys.append(key)
        else:
            blocked.append({"knowledge_key": key, "state": "UNASSESSED"})

    if blocked:
        return {
            "schema": "KDO.R13H.RuntimeAdmission/v1",
            "status": "BLOCKED",
            "admitted": False,
            "workspace_id": workspace_id,
            "question": question,
            "reason": "runtime_context_contains_knowledge_that_is_not_ready",
            "blocked_requirements": blocked,
            "context": [],
            "citations": [],
            "runtime_authority": "existing_agent_runtime",
            "safety": _safety(),
        }

    context: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict) or not _active(fact) or not _confirmed(fact):
            continue
        key = _fact_key(fact)
        if key not in admitted_keys:
            continue
        if require_fresh and fact.get("freshness") in {"stale", "unknown"}:
            blocked.append({"knowledge_key": key, "state": "STALE_OR_UNKNOWN_FRESHNESS"})
            continue
        context.append({
            "fact_id": fact.get("id") or fact.get("fact_id"),
            "knowledge_key": key,
            "value": fact.get("value"),
            "predicate": fact.get("predicate"),
            "citations": [_citation(fact)],
        })

    if blocked:
        return {
            "schema": "KDO.R13H.RuntimeAdmission/v1",
            "status": "BLOCKED",
            "admitted": False,
            "workspace_id": workspace_id,
            "question": question,
            "reason": "runtime_context_failed_freshness_requirement",
            "blocked_requirements": blocked,
            "context": [],
            "citations": [],
            "runtime_authority": "existing_agent_runtime",
            "safety": _safety(),
        }

    if not context:
        return {
            "schema": "KDO.R13H.RuntimeAdmission/v1",
            "status": "BLOCKED",
            "admitted": False,
            "workspace_id": workspace_id,
            "question": question,
            "reason": "no_confirmed_current_context_available_for_runtime",
            "blocked_requirements": [{"knowledge_key": k, "state": "NO_CURRENT_FACT"} for k in admitted_keys],
            "context": [],
            "citations": [],
            "runtime_authority": "existing_agent_runtime",
            "safety": _safety(),
        }

    return {
        "schema": "KDO.R13H.RuntimeAdmission/v1",
        "status": "ADMITTED",
        "admitted": True,
        "workspace_id": workspace_id,
        "question": question,
        "reason": None,
        "blocked_requirements": [],
        "context": context,
        "citations": [c for item in context for c in item["citations"]],
        "runtime_authority": "existing_agent_runtime",
        "safety": _safety(),
    }


def _safety() -> dict[str, bool]:
    return {
        "read_only": True,
        "does_not_create_knowledge": True,
        "does_not_confirm_knowledge": True,
        "does_not_supersede_facts": True,
        "does_not_authorize_actions": True,
        "does_not_execute_providers": True,
        "does_not_replace_agent_runtime": True,
        "requires_existing_runtime_authority": True,
    }
