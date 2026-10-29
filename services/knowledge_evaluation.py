"""R13-G knowledge evaluation planner.

Read-only assessment of the existing Business/Knowledge Plane. It measures
coverage, confirmation, evidence support, freshness, conflicts, and
requirement readiness without creating or mutating knowledge.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _status(fact: dict[str, Any]) -> str:
    return _norm(fact.get("status") or "confirmed")


def _active(fact: dict[str, Any]) -> bool:
    return _status(fact) not in {"superseded", "revoked", "archived"} and not bool(fact.get("superseded"))


def _confirmed(fact: dict[str, Any]) -> bool:
    return _status(fact) == "confirmed" and _active(fact)


def _evidence_count(fact: dict[str, Any]) -> int:
    evidence = fact.get("evidence_ids") or fact.get("evidence") or []
    if isinstance(evidence, (list, tuple, set)):
        return len(evidence)
    return 1 if evidence else 0


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError):
        return None


def _freshness(fact: dict[str, Any], now: datetime) -> str:
    raw = fact.get("updated_at") or fact.get("confirmed_at") or fact.get("observed_at")
    dt = _parse_dt(raw)
    if not dt:
        return "unknown"
    age_days = max(0, (now - dt.astimezone(timezone.utc)).total_seconds() / 86400)
    if age_days <= 30:
        return "fresh"
    if age_days <= 180:
        return "aging"
    return "stale"


def _requirement_key(req: Any) -> str:
    if isinstance(req, str):
        return _norm(req)
    if isinstance(req, dict):
        return _norm(req.get("knowledge_key") or req.get("key") or req.get("requirement") or req.get("subject"))
    return ""


def evaluate_knowledge(
    facts: list[dict[str, Any]],
    requirements: list[Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a deterministic, read-only knowledge quality/readiness assessment."""
    now = now or datetime.now(timezone.utc)
    active = [f for f in (facts or []) if isinstance(f, dict) and _active(f)]
    confirmed = [f for f in active if _confirmed(f)]

    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in active:
        key = _norm(fact.get("knowledge_key") or fact.get("requirement") or fact.get("key") or fact.get("subject"))
        if key:
            by_key[key].append(fact)

    conflict_keys = []
    unsupported_confirmed = []
    stale_confirmed = []
    for key, group in by_key.items():
        values = {_norm(f.get("value")) for f in group if f.get("value") is not None}
        if len(values) > 1 and all(_confirmed(f) for f in group):
            conflict_keys.append(key)
        for fact in group:
            if _confirmed(fact) and _evidence_count(fact) == 0:
                unsupported_confirmed.append(fact.get("id"))
            if _confirmed(fact) and _freshness(fact, now) == "stale":
                stale_confirmed.append(fact.get("id"))

    requirement_results = []
    for raw_req in requirements or []:
        key = _requirement_key(raw_req)
        matches = by_key.get(key, []) if key else []
        active_matches = [f for f in matches if _active(f)]
        confirmed_matches = [f for f in active_matches if _confirmed(f)]
        if not key:
            state = "INVALID_REQUIREMENT"
        elif not active_matches:
            state = "MISSING"
        elif len({_norm(f.get("value")) for f in active_matches}) > 1 and all(_confirmed(f) for f in active_matches):
            state = "CONFLICT"
        elif not confirmed_matches:
            state = "UNCONFIRMED"
        elif any(_evidence_count(f) == 0 for f in confirmed_matches):
            state = "CONFIRMED_UNSUPPORTED"
        else:
            state = "READY"
        requirement_results.append({
            "knowledge_key": key,
            "state": state,
            "fact_ids": [f.get("id") for f in active_matches],
            "next_action": {
                "MISSING": "discover_and_request_evidence",
                "UNCONFIRMED": "review_and_confirm",
                "CONFLICT": "resolve_conflict",
                "CONFIRMED_UNSUPPORTED": "request_or_attach_evidence",
                "READY": "none",
                "INVALID_REQUIREMENT": "clarify_requirement",
            }[state],
        })

    coverage = None
    valid_requirements = [r for r in requirement_results if r["state"] != "INVALID_REQUIREMENT"]
    if valid_requirements:
        ready = sum(r["state"] == "READY" for r in valid_requirements)
        coverage = round(ready / len(valid_requirements), 4)

    return {
        "schema": "KDO.R13G.KnowledgeEvaluation/v1",
        "read_only": True,
        "summary": {
            "active_fact_count": len(active),
            "confirmed_fact_count": len(confirmed),
            "requirement_count": len(valid_requirements),
            "requirement_coverage": coverage,
            "conflict_count": len(conflict_keys),
            "unsupported_confirmed_count": len(unsupported_confirmed),
            "stale_confirmed_count": len(stale_confirmed),
        },
        "quality": {
            "has_confirmed_conflicts": bool(conflict_keys),
            "has_unsupported_confirmed_facts": bool(unsupported_confirmed),
            "has_stale_confirmed_facts": bool(stale_confirmed),
        },
        "requirements": requirement_results,
        "attention": {
            "conflict_keys": conflict_keys,
            "unsupported_fact_ids": unsupported_confirmed,
            "stale_fact_ids": stale_confirmed,
        },
        "safety": {
            "does_not_create_knowledge": True,
            "does_not_confirm_knowledge": True,
            "does_not_supersede_facts": True,
            "does_not_delete_evidence": True,
            "does_not_authorize_actions": True,
            "does_not_execute_providers": True,
        },
    }
