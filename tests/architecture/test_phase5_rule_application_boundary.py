import ast
import asyncio
from pathlib import Path

from application.rules import InMemoryRuleStore, RuleManagementApplicationService

ROOT = Path(__file__).resolve().parents[2]


class FakeEngine:
    def __init__(self):
        self._decisions = {}

    def get_status(self):
        return {"observations": 2, "decisions": 1, "experiments": 0, "strategies": 1}


def test_rule_application_service_owns_rule_use_cases():
    store = InMemoryRuleStore()
    service = RuleManagementApplicationService(store, engine=type("E", (), {"list_rules": lambda s: None, "status": lambda s: None})())
    created = service.create({
        "id": "r1", "name": "High load", "status": "active",
        "when": {"all": [{"field": "load", "op": "gte", "value": 80}]},
        "then": [],
    })
    assert created["ok"] is True
    assert service.test("r1", {"load": 90})["matched"] is True
    assert service.test_all({"load": 90})["matching_rules"][0]["rule_id"] == "r1"
    assert service.toggle("r1", "paused")["status"] == "paused"
    assert service.update("r1", {"name": "Updated", "status": "active", "when": {"all": []}, "then": []})["rule"]["created_at"]
    assert service.delete("r1")["ok"] is True


def test_rule_application_layer_has_no_http_dependencies():
    tree = ast.parse((ROOT / "application" / "rules.py").read_text())
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = []
    for node in imports:
        if isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
        else:
            names.append(node.module or "")
    assert not any(name.startswith(("fastapi", "starlette", "pydantic")) for name in names)


def test_rule_route_is_http_adapter_not_condition_engine_owner():
    source = (ROOT / "routes" / "rule_routes.py").read_text()
    assert "from services.condition_engine import autonomous_engine" in source
    assert "RuleManagementApplicationService" in source
    assert "engine._decisions" not in source
    assert "datetime.now" not in source
    assert "def _evaluate_rule_local" not in source


def test_engine_adapter_preserves_existing_engine_read_model():
    engine = FakeEngine()
    engine._decisions["decision-1"] = type("Decision", (), {"name": "Decision", "reason": "reason", "risk": "medium"})()
    from application.rules import ConditionEngineAdapter
    adapter = ConditionEngineAdapter(engine)
    result = adapter.list_rules()
    assert result[0]["id"] == "decision-1"
    assert adapter.status()["decisions"] == 1


def test_metrics_and_health_remain_application_use_cases():
    store = InMemoryRuleStore()
    store.put("r1", {"id": "r1", "status": "active"})
    service = RuleManagementApplicationService(store, engine=type("E", (), {"list_rules": lambda s: None, "status": lambda s: {"decisions": 3}})())
    metrics = service.metrics()
    health = service.health()
    assert metrics["total_rules"] == 1
    assert metrics["decisions"] == 3
    assert health["status"] == "healthy"
