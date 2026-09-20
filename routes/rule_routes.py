"""
Rule Engine API Routes — v2.0
REST endpoints for the Autonomous Condition Engine.

Endpoints:
    GET    /rules              — List all rules
    POST   /rules              — Create a new rule
    PUT    /rules/:id          — Update an existing rule
    DELETE /rules/:id          — Delete a rule
    POST   /rules/:id/toggle   — Toggle rule active/paused
    POST   /rules/:id/test     — Dry-run a single rule
    POST   /rules/test         — Test all rules against context
    GET    /rules/history      — Execution history
    GET    /rules/metrics      — Engine telemetry
    GET    /rules/health       — Engine health status
    GET    /rules/events       — Recent processed events
    POST   /rules/events/simulate — Simulate event against all rules
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("kanyoza.routes.rules")

from core.security.deps import require_species_privileges

router = APIRouter(prefix="/api/v1/rules", tags=["rules"], dependencies=[Depends(require_species_privileges)])


# ═══════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════

class ConditionModel(BaseModel):
    field: str
    op: str
    value: Any = None


class ConditionGroupModel(BaseModel):
    all: Optional[List[ConditionModel]] = None
    any: Optional[List[ConditionModel]] = None


class RuleActionModel(BaseModel):
    action: str
    value: Any = None
    params: Optional[Dict[str, Any]] = None


class RuleModel(BaseModel):
    id: str
    name: str
    description: str = ""
    priority: str = "medium"
    status: str = "active"
    when: ConditionGroupModel
    then: List[RuleActionModel]
    cooldown_seconds: int = 0
    max_firings_per_hour: Optional[int] = None
    max_firings_per_day: Optional[int] = None
    tags: Optional[List[str]] = None
    firing_count: Optional[int] = 0
    success_count: Optional[int] = 0
    failure_count: Optional[int] = 0
    last_fired_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ToggleRequest(BaseModel):
    status: str


class TestRequest(BaseModel):
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


class SimulateRequest(BaseModel):
    event: str
    context: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


# ═══════════════════════════════════════════════════════════════════════════
# IN-MEMORY STORE (falls back when Supabase unavailable)
# ═══════════════════════════════════════════════════════════════════════════

_rules_store: Dict[str, Dict[str, Any]] = {}
_firing_history: List[Dict[str, Any]] = []
_event_log: List[Dict[str, Any]] = []


def _get_engine():
    """Get the condition engine singleton if available."""
    try:
        from services.condition_engine import autonomous_engine
        return autonomous_engine
    except ImportError:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/")
async def list_rules():
    """List all rules."""
    engine = _get_engine()

    if engine:
        try:
            rules = []
            for rule_id, rule in engine._decisions.items():
                rules.append({
                    "id": rule_id,
                    "name": getattr(rule, "name", rule_id),
                    "description": getattr(rule, "reason", ""),
                    "priority": getattr(rule, "risk", "medium"),
                    "status": "active",
                    "when": {"all": []},
                    "then": [],
                    "cooldown_seconds": 0,
                    "created_at": getattr(rule, "created_at", _utc_now()),
                })
            return {"rules": rules}
        except Exception as exc:
            logger.warning("Engine rule listing failed: %s", exc)

    return {"rules": list(_rules_store.values())}


@router.post("/")
async def create_rule(rule: RuleModel):
    """Create a new rule."""
    rule_data = rule.model_dump()

    if not rule_data.get("id"):
        rule_data["id"] = f"rule_{int(datetime.now(timezone.utc).timestamp())}_{id(rule_data)}"

    rule_data["created_at"] = _utc_now()
    rule_data["updated_at"] = _utc_now()

    _rules_store[rule_data["id"]] = rule_data

    logger.info("Rule created: %s (%s)", rule_data["name"], rule_data["id"])
    return {"ok": True, "rule": rule_data}


@router.put("/{rule_id}")
async def update_rule(rule_id: str, rule: RuleModel):
    """Update an existing rule."""
    rule_data = rule.model_dump()
    rule_data["id"] = rule_id
    rule_data["updated_at"] = _utc_now()

    existing = _rules_store.get(rule_id, {})
    rule_data["created_at"] = existing.get("created_at", _utc_now())
    rule_data["firing_count"] = existing.get("firing_count", 0)
    rule_data["success_count"] = existing.get("success_count", 0)
    rule_data["failure_count"] = existing.get("failure_count", 0)

    _rules_store[rule_id] = rule_data

    logger.info("Rule updated: %s (%s)", rule_data["name"], rule_id)
    return {"ok": True, "rule": rule_data}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str):
    """Delete a rule."""
    _rules_store.pop(rule_id, None)
    logger.info("Rule deleted: %s", rule_id)
    return {"ok": True}


@router.post("/{rule_id}/toggle")
async def toggle_rule(rule_id: str, request: ToggleRequest):
    """Toggle a rule between active and paused."""
    rule = _rules_store.get(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    rule["status"] = request.status
    rule["updated_at"] = _utc_now()
    _rules_store[rule_id] = rule

    logger.info("Rule toggled: %s → %s", rule_id, request.status)
    return {"ok": True, "status": request.status}


@router.post("/{rule_id}/test")
async def test_rule(rule_id: str, request: TestRequest):
    """Dry-run a single rule against a test context."""
    rule = _rules_store.get(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    result = _evaluate_rule_local(rule, request.context)
    result["rule_id"] = rule_id
    result["rule_name"] = rule.get("name", "")

    return result


@router.post("/test")
async def test_all_rules(request: TestRequest):
    """Test all active rules against a context."""
    results = []
    for rule_id, rule in _rules_store.items():
        if rule.get("status") != "active":
            continue
        result = _evaluate_rule_local(rule, request.context)
        if result.get("matched"):
            results.append({
                "rule_id": rule_id,
                "rule_name": rule.get("name", ""),
                "matched": True,
            })

    return {
        "matched": len(results) > 0,
        "matching_rules": results,
        "total_rules_tested": len([r for r in _rules_store.values() if r.get("status") == "active"]),
    }


@router.get("/history")
async def get_history(limit: int = Query(50, ge=1, le=200)):
    """Get rule execution history."""
    return {"firings": _firing_history[-limit:]}


@router.get("/metrics")
async def get_metrics():
    """Get engine telemetry."""
    engine = _get_engine()
    if engine:
        try:
            status = engine.get_status()
            return {
                "total_rules": len(_rules_store),
                "active_rules": len([r for r in _rules_store.values() if r.get("status") == "active"]),
                "paused_rules": len([r for r in _rules_store.values() if r.get("status") == "paused"]),
                "disabled_rules": len([r for r in _rules_store.values() if r.get("status") == "disabled"]),
                "firings_24h": len([f for f in _firing_history if _is_within_24h(f.get("fired_at", ""))]),
                "successful_firings_24h": len([f for f in _firing_history if f.get("success") and _is_within_24h(f.get("fired_at", ""))]),
                "failed_firings_24h": len([f for f in _firing_history if not f.get("success") and _is_within_24h(f.get("fired_at", ""))]),
                "success_rate": _calc_success_rate(),
                "avg_execution_ms": 0,
                "events_processed_24h": len(_event_log),
                "observations": status.get("observations", 0),
                "decisions": status.get("decisions", 0),
                "experiments": status.get("experiments", 0),
                "strategies": status.get("strategies", 0),
            }
        except Exception as exc:
            logger.warning("Engine metrics failed: %s", exc)

    return {
        "total_rules": len(_rules_store),
        "active_rules": len([r for r in _rules_store.values() if r.get("status") == "active"]),
        "paused_rules": len([r for r in _rules_store.values() if r.get("status") == "paused"]),
        "disabled_rules": len([r for r in _rules_store.values() if r.get("status") == "disabled"]),
        "firings_24h": 0,
        "successful_firings_24h": 0,
        "failed_firings_24h": 0,
        "success_rate": 0,
        "avg_execution_ms": 0,
    }


@router.get("/health")
async def get_health():
    """Get engine health status."""
    engine = _get_engine()
    return {
        "status": "healthy" if engine else "degraded",
        "version": "2.0",
        "rules_loaded": len(_rules_store),
        "uptime_seconds": 0,
        "events_processed": len(_event_log),
        "queue_depth": 0,
        "error_count": 0,
    }


@router.get("/events")
async def get_events(limit: int = Query(30, ge=1, le=100)):
    """Get recent processed events."""
    return {"events": _event_log[-limit:]}


@router.post("/events/simulate")
async def simulate_event(request: SimulateRequest):
    """Simulate an event against all active rules."""
    context = request.context
    context["event"] = request.event

    results = []
    for rule_id, rule in _rules_store.items():
        if rule.get("status") != "active":
            continue
        result = _evaluate_rule_local(rule, context)
        if result.get("matched"):
            results.append({
                "rule_id": rule_id,
                "rule_name": rule.get("name", ""),
                "matched": True,
                "actions": rule.get("then", []),
            })

    # Log the event
    _event_log.append({
        "id": f"event_{int(datetime.now(timezone.utc).timestamp())}",
        "event": request.event,
        "timestamp": _utc_now(),
        "matched_rules": len(results),
        "context": {k: str(v)[:100] for k, v in context.items()},
    })

    if len(_event_log) > 200:
        _event_log.pop(0)

    return {
        "matched": len(results) > 0,
        "matching_rules": results,
        "total_rules_evaluated": len([r for r in _rules_store.values() if r.get("status") == "active"]),
        "event": request.event,
    }


# ═══════════════════════════════════════════════════════════════════════════
# LOCAL RULE EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

def _evaluate_rule_local(rule: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate a rule against a context locally."""
    when = rule.get("when", {})
    conditions = (when.get("all") or []) + (when.get("any") or [])
    all_mode = bool(when.get("all"))

    details = []
    for condition in conditions:
        field = condition.get("field", "")
        op = condition.get("op", "eq")
        expected = condition.get("value")
        actual = context.get(field)

        matched = _evaluate_condition(actual, op, expected)

        details.append({
            "field": field,
            "operator": op,
            "expected": expected,
            "actual": actual,
            "matched": matched,
        })

    if all_mode:
        matched = all(d.get("matched") for d in details) if details else False
    else:
        matched = any(d.get("matched") for d in details) if details else False

    return {
        "matched": matched,
        "conditions": details,
        "actions": rule.get("then", []) if matched else [],
        "explanation": "All conditions matched." if matched else "One or more conditions did not match.",
    }


def _evaluate_condition(actual: Any, operator: str, expected: Any) -> bool:
    """Evaluate a single condition."""
    try:
        if operator == "eq":
            return str(actual).lower() == str(expected).lower()
        if operator == "neq":
            return str(actual).lower() != str(expected).lower()
        if operator == "gt":
            return float(actual) > float(expected)
        if operator == "gte":
            return float(actual) >= float(expected)
        if operator == "lt":
            return float(actual) < float(expected)
        if operator == "lte":
            return float(actual) <= float(expected)
        if operator == "in":
            return actual in (expected if isinstance(expected, (list, tuple)) else [expected])
        if operator == "not_in":
            return actual not in (expected if isinstance(expected, (list, tuple)) else [expected])
        if operator == "contains":
            return str(expected).lower() in str(actual).lower()
        if operator == "not_contains":
            return str(expected).lower() not in str(actual).lower()
        if operator == "starts_with":
            return str(actual).lower().startswith(str(expected).lower())
        if operator == "ends_with":
            return str(actual).lower().endswith(str(expected).lower())
        if operator == "between":
            if isinstance(expected, (list, tuple)) and len(expected) >= 2:
                val = float(actual)
                return float(expected[0]) <= val <= float(expected[1])
            return False
        if operator == "exists":
            return actual is not None
        if operator == "not_exists":
            return actual is None
        if operator == "regex":
            import re
            return bool(re.search(str(expected), str(actual), re.IGNORECASE))
    except (ValueError, TypeError):
        pass
    return False


def _is_within_24h(timestamp: str) -> bool:
    """Check if a timestamp is within the last 24 hours."""
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() < 86400
    except Exception:
        return False


def _calc_success_rate() -> float:
    """Calculate success rate from history."""
    total = len(_firing_history)
    if total == 0:
        return 0
    successful = len([f for f in _firing_history if f.get("success")])
    return round((successful / total) * 100, 1)
