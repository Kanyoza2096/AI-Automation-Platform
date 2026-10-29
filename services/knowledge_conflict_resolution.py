"""R13-F knowledge conflict resolution planner.

Analyzes competing knowledge representations without creating, mutating, or
confirming Knowledge Plane truth. Existing Knowledge Plane confirmation and
supersession mechanisms remain authoritative.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _display(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _fact_key(fact: dict[str, Any]) -> str:
    return _norm(fact.get("knowledge_key") or fact.get("requirement") or fact.get("key") or fact.get("subject"))


def _status(fact: dict[str, Any]) -> str:
    return _norm(fact.get("status") or "confirmed")


def _is_superseded(fact: dict[str, Any]) -> bool:
    return _status(fact) in {"superseded", "revoked", "archived"} or bool(fact.get("superseded"))


def _authority(fact: dict[str, Any]) -> str:
    # Only explicit metadata is considered. We never infer authority from a URL/domain.
    return _norm(fact.get("authority") or fact.get("source_authority") or "")


def _candidate_conflict(left: dict[str, Any], right: dict[str, Any]) -> str:
    left_status, right_status = _status(left), _status(right)
    if left_status in {"candidate", "extracted", "unconfirmed", "pending"} or right_status in {"candidate", "extracted", "unconfirmed", "pending"}:
        return "candidate_against_confirmed" if {left_status, right_status} != {"candidate", "extracted"} else "candidate_disagreement"
    return "confirmed_conflict"


def _recommendation(group: list[dict[str, Any]], conflict_type: str) -> dict[str, Any]:
    explicit_authoritative = [f for f in group if _authority(f) in {"authoritative", "authoritative_source", "system_of_record"}]
    if conflict_type == "confirmed_conflict" and len(explicit_authoritative) == 1:
        winner = explicit_authoritative[0]
        return {
            "mode": "confirm_authoritative_precedence",
            "preferred_fact_id": winner.get("id"),
            "requires_explicit_confirmation": True,
            "auto_resolve": False,
            "reason": "One competing fact is explicitly marked authoritative; KDO should present that precedence for confirmation rather than silently changing truth.",
        }
    return {
        "mode": "request_resolution",
        "preferred_fact_id": None,
        "requires_explicit_confirmation": True,
        "auto_resolve": False,
        "reason": "No safe deterministic precedence was established from explicit metadata.",
    }


def analyze_knowledge_conflicts(
    facts: list[dict[str, Any]],
    *,
    include_superseded: bool = False,
) -> dict[str, Any]:
    """Return deterministic conflict analysis for existing Knowledge Plane facts.

    This is a read-only planner. It does not write facts, mark anything
    superseded, confirm knowledge, or execute business actions.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ignored = []
    for raw in facts or []:
        if not isinstance(raw, dict):
            continue
        if _is_superseded(raw) and not include_superseded:
            ignored.append({"id": raw.get("id"), "reason": "superseded_or_inactive"})
            continue
        key = _fact_key(raw)
        value = _norm(raw.get("value"))
        if not key or not value:
            ignored.append({"id": raw.get("id"), "reason": "missing_knowledge_key_or_value"})
            continue
        item = dict(raw)
        item["normalized_value"] = value
        groups[key].append(item)

    conflicts = []
    consistent = []
    for key, group in groups.items():
        values = {f["normalized_value"] for f in group}
        if len(values) <= 1:
            consistent.append({"knowledge_key": key, "fact_ids": [f.get("id") for f in group]})
            continue
        conflict_type = "confirmed_conflict"
        if any(_status(f) in {"candidate", "extracted", "unconfirmed", "pending"} for f in group):
            conflict_type = "candidate_against_confirmed"
        recommendation = _recommendation(group, conflict_type)
        conflicts.append({
            "knowledge_key": key,
            "conflict_type": conflict_type,
            "facts": [
                {
                    "id": f.get("id"),
                    "value": _display(f.get("value")),
                    "status": _status(f),
                    "authority": _authority(f) or None,
                    "source": f.get("source") or f.get("source_type"),
                    "evidence_ids": f.get("evidence_ids") or [],
                }
                for f in group
            ],
            "recommendation": recommendation,
        })

    return {
        "schema": "KDO.R13F.KnowledgeConflictAnalysis/v1",
        "read_only": True,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "consistent_groups": consistent,
        "ignored": ignored,
        "safety": {
            "does_not_create_knowledge": True,
            "does_not_confirm_knowledge": True,
            "does_not_supersede_facts": True,
            "does_not_delete_evidence": True,
            "does_not_authorize_actions": True,
            "does_not_execute_providers": True,
        },
    }
