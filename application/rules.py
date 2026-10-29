"""Application boundary for condition/rule management.

The application layer owns rule-management use cases. HTTP, the condition-engine
singleton, and persistence/fallback details are kept behind ports/adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol


class RuleStorePort(Protocol):
    def list(self) -> List[Dict[str, Any]]: ...
    def get(self, rule_id: str) -> Optional[Dict[str, Any]]: ...
    def put(self, rule_id: str, rule: Dict[str, Any]) -> None: ...
    def delete(self, rule_id: str) -> None: ...
    def history(self) -> List[Dict[str, Any]]: ...
    def events(self) -> List[Dict[str, Any]]: ...


class ConditionEnginePort(Protocol):
    def list_rules(self) -> Optional[List[Dict[str, Any]]]: ...
    def status(self) -> Optional[Dict[str, Any]]: ...


@dataclass
class InMemoryRuleStore:
    rules: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    firing_history: List[Dict[str, Any]] = field(default_factory=list)
    event_log: List[Dict[str, Any]] = field(default_factory=list)

    def list(self) -> List[Dict[str, Any]]:
        return list(self.rules.values())

    def get(self, rule_id: str) -> Optional[Dict[str, Any]]:
        return self.rules.get(rule_id)

    def put(self, rule_id: str, rule: Dict[str, Any]) -> None:
        self.rules[rule_id] = rule

    def delete(self, rule_id: str) -> None:
        self.rules.pop(rule_id, None)

    def history(self) -> List[Dict[str, Any]]:
        return self.firing_history

    def events(self) -> List[Dict[str, Any]]:
        return self.event_log


class ConditionEngineAdapter:
    """Infrastructure adapter for the existing condition-engine singleton."""

    def __init__(self, engine: Any = None):
        self._engine = engine

    def _current_engine(self) -> Any:
        return self._engine() if callable(self._engine) else self._engine

    def list_rules(self) -> Optional[List[Dict[str, Any]]]:
        engine = self._current_engine()
        if not engine:
            return None
        try:
            return [
                {
                    "id": rule_id,
                    "name": getattr(rule, "name", rule_id),
                    "description": getattr(rule, "reason", ""),
                    "priority": getattr(rule, "risk", "medium"),
                    "status": "active",
                    "when": {"all": []},
                    "then": [],
                    "cooldown_seconds": 0,
                    "created_at": getattr(rule, "created_at", _utc_now()),
                }
                for rule_id, rule in engine._decisions.items()
            ]
        except Exception:
            return None

    def status(self) -> Optional[Dict[str, Any]]:
        engine = self._current_engine()
        if not engine:
            return None
        try:
            return engine.get_status()
        except Exception:
            return None


class RuleApplicationError(Exception):
    """Base application error for rule use cases."""


class RuleNotFoundError(RuleApplicationError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evaluate_rule_local(rule: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    conditions = rule.get("when", {}) or {}
    all_conditions = conditions.get("all") or []
    any_conditions = conditions.get("any") or []

    def matches(condition: Dict[str, Any]) -> bool:
        field = condition.get("field", "")
        op = condition.get("op", "eq")
        expected = condition.get("value")
        actual = context.get(field)
        if op in ("eq", "equals"):
            return actual == expected
        if op == "neq":
            return actual != expected
        if op == "gt":
            return actual is not None and actual > expected
        if op == "gte":
            return actual is not None and actual >= expected
        if op == "lt":
            return actual is not None and actual < expected
        if op == "lte":
            return actual is not None and actual <= expected
        if op == "contains":
            return expected in actual if actual is not None else False
        return False

    all_match = all(matches(c) for c in all_conditions)
    any_match = not any_conditions or any(matches(c) for c in any_conditions)
    return {"matched": all_match and any_match, "dry_run": True}


class RuleManagementApplicationService:
    def __init__(self, store: RuleStorePort, engine: ConditionEnginePort):
        self.store = store
        self.engine = engine

    def list_rules(self) -> Dict[str, Any]:
        engine_rules = self.engine.list_rules()
        return {"rules": engine_rules if engine_rules is not None else self.store.list()}

    def create(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(rule)
        data.setdefault("id", f"rule_{int(datetime.now(timezone.utc).timestamp())}_{id(data)}")
        data["created_at"] = _utc_now()
        data["updated_at"] = data["created_at"]
        self.store.put(data["id"], data)
        return {"ok": True, "rule": data}

    def update(self, rule_id: str, rule: Dict[str, Any]) -> Dict[str, Any]:
        existing = self.store.get(rule_id)
        data = dict(rule)
        data["id"] = rule_id
        data["updated_at"] = _utc_now()
        data["created_at"] = (existing or {}).get("created_at", _utc_now())
        for key in ("firing_count", "success_count", "failure_count"):
            data[key] = (existing or {}).get(key, 0)
        self.store.put(rule_id, data)
        return {"ok": True, "rule": data}

    def delete(self, rule_id: str) -> Dict[str, Any]:
        self.store.delete(rule_id)
        return {"ok": True}

    def toggle(self, rule_id: str, status: str) -> Dict[str, Any]:
        rule = self.store.get(rule_id)
        if not rule:
            raise RuleNotFoundError(rule_id)
        rule = dict(rule)
        rule["status"] = status
        rule["updated_at"] = _utc_now()
        self.store.put(rule_id, rule)
        return {"ok": True, "status": status}

    def test(self, rule_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        rule = self.store.get(rule_id)
        if not rule:
            raise RuleNotFoundError(rule_id)
        result = _evaluate_rule_local(rule, context)
        result.update({"rule_id": rule_id, "rule_name": rule.get("name", "")})
        return result

    def test_all(self, context: Dict[str, Any]) -> Dict[str, Any]:
        results = []
        active = [r for r in self.store.list() if r.get("status") == "active"]
        for rule in active:
            result = _evaluate_rule_local(rule, context)
            if result.get("matched"):
                results.append({"rule_id": rule.get("id"), "rule_name": rule.get("name", ""), "matched": True})
        return {"matched": bool(results), "matching_rules": results, "total_rules_tested": len(active)}

    def history(self, limit: int) -> Dict[str, Any]:
        return {"firings": self.store.history()[-limit:]}

    def metrics(self) -> Dict[str, Any]:
        rules = self.store.list()
        history = self.store.history()
        engine_status = self.engine.status() or {}
        recent = [f for f in history if _within_24h(f.get("fired_at", ""))]
        successful = [f for f in recent if f.get("success")]
        return {
            "total_rules": len(rules),
            "active_rules": len([r for r in rules if r.get("status") == "active"]),
            "paused_rules": len([r for r in rules if r.get("status") == "paused"]),
            "disabled_rules": len([r for r in rules if r.get("status") == "disabled"]),
            "firings_24h": len(recent),
            "successful_firings_24h": len(successful),
            "failed_firings_24h": len(recent) - len(successful),
            "success_rate": (len(successful) / len(recent) * 100) if recent else 0,
            "avg_execution_ms": 0,
            "events_processed_24h": len(self.store.events()),
            **{k: engine_status.get(k, 0) for k in ("observations", "decisions", "experiments", "strategies")},
        }

    def health(self) -> Dict[str, Any]:
        engine_available = self.engine.status() is not None
        return {
            "status": "healthy" if engine_available else "degraded",
            "version": "2.0",
            "rules_loaded": len(self.store.list()),
            "uptime_seconds": 0,
            "events_processed": len(self.store.events()),
            "queue_depth": 0,
            "error_count": 0,
        }


def _within_24h(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - parsed).total_seconds() <= 86400
    except (TypeError, ValueError):
        return False
