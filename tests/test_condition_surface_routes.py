import asyncio
from types import SimpleNamespace

import services.condition_engine as condition_engine
from services.condition_engine.models import Baseline, Decision, DecisionState, Experiment
from routes import cp_surface_routes


def allow_workspace(monkeypatch, brand_ids=("brand-1",)):
    async def resolve_workspace_brands(request, container):
        return set(brand_ids)

    monkeypatch.setattr(cp_surface_routes, "_workspace_brand_ids", resolve_workspace_brands)


def test_condition_baselines_serialize_keyed_dataclass_like_records(monkeypatch):
    engine = SimpleNamespace(
        _baselines={
            ("brand-1", "engagement_rate"): {
                "metric": "engagement_rate",
                "mean": 0.24,
                "sample_count": 12,
            },
            ("brand-2", "engagement_rate"): {
                "metric": "engagement_rate",
                "mean": 0.91,
                "sample_count": 4,
            }
        }
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)
    allow_workspace(monkeypatch)

    result = asyncio.run(cp_surface_routes.condition_baselines(None, None))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["baselines"][0]["id"] == "brand-1:engagement_rate"
    assert result["baselines"][0]["brand_id"] == "brand-1"
    assert result["baselines"][0]["mean"] == 0.24


def test_condition_experiments_serialize_dictionary_values_not_keys(monkeypatch):
    engine = SimpleNamespace(
        _experiments={
            "experiment-1": {
                "id": "experiment-1",
                "brand_id": "brand-1",
                "name": "Landing page test",
                "control": "A",
                "variant": "B",
                "status": "running",
            }
        }
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)
    allow_workspace(monkeypatch)

    result = asyncio.run(cp_surface_routes.condition_experiments(None, None))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["experiments"][0]["id"] == "experiment-1"
    assert result["experiments"][0]["control"] == "A"


def test_condition_decisions_uses_actual_decision_store(monkeypatch):
    engine = SimpleNamespace(
        _decisions={
            "decision-1": {
                "id": "decision-1",
                "brand_id": "brand-1",
                "reason": "Engagement dropped",
                "recommended_action": "Review posting cadence",
                "status": "proposed",
            }
        },
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)
    allow_workspace(monkeypatch)

    result = asyncio.run(cp_surface_routes.condition_decisions(None, None))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["decisions"][0]["id"] == "decision-1"
    assert result["decisions"][0]["reason"] == "Engagement dropped"
    assert result["status"]["workspace_brand_count"] == 1


def test_valid_empty_condition_stores_remain_valid_empty_collections(monkeypatch):
    engine = SimpleNamespace(_baselines={}, _experiments={}, _decisions={})
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)
    allow_workspace(monkeypatch)

    baselines = asyncio.run(cp_surface_routes.condition_baselines(None, None))
    experiments = asyncio.run(cp_surface_routes.condition_experiments(None, None))
    decisions = asyncio.run(cp_surface_routes.condition_decisions(None, None))

    assert baselines["ok"] is True and baselines["baselines"] == []
    assert experiments["ok"] is True and experiments["experiments"] == []
    assert decisions["ok"] is True and decisions["decisions"] == []


def test_missing_condition_provider_is_not_reported_as_successful_empty(monkeypatch):
    monkeypatch.setattr(condition_engine, "autonomous_engine", SimpleNamespace())
    allow_workspace(monkeypatch)

    baselines = asyncio.run(cp_surface_routes.condition_baselines(None, None))
    experiments = asyncio.run(cp_surface_routes.condition_experiments(None, None))
    decisions = asyncio.run(cp_surface_routes.condition_decisions(None, None))

    assert baselines["ok"] is False and baselines["source"] == "error"
    assert experiments["ok"] is False and experiments["source"] == "error"
    assert decisions["ok"] is False and decisions["source"] == "error"


def test_condition_provider_exception_is_not_reported_as_successful_empty(monkeypatch):
    class BrokenConditionEngine:
        def get_baselines(self):
            raise RuntimeError("internal provider details must not be exposed")

    monkeypatch.setattr(condition_engine, "autonomous_engine", BrokenConditionEngine())
    allow_workspace(monkeypatch)

    result = asyncio.run(cp_surface_routes.condition_baselines(None, None))

    assert result["ok"] is False
    assert result["source"] == "error"
    assert result["error"] == "RuntimeError"
    assert result["message"] == "Condition baseline evidence is unavailable."
    assert "internal provider details" not in str(result)


def test_condition_engine_dataclasses_are_json_serialized(monkeypatch):
    engine = SimpleNamespace(
        _baselines={
            ("brand-1", "engagement_rate"): Baseline(metric="engagement_rate", mean=0.31, sample_count=8)
        },
        _experiments={
            "experiment-1": Experiment(
                id="experiment-1",
                brand_id="brand-1",
                name="Landing page test",
                variable="headline",
                control="A",
                variant="B",
            )
        },
        _decisions={
            "decision-1": Decision(
                id="decision-1",
                brand_id="brand-1",
                state=DecisionState.OBSERVE,
                confidence=0.82,
                reason="Engagement evidence is incomplete",
                evidence={"observations": 3},
            )
        },
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)
    allow_workspace(monkeypatch)

    baselines = asyncio.run(cp_surface_routes.condition_baselines(None, None))
    experiments = asyncio.run(cp_surface_routes.condition_experiments(None, None))
    decisions = asyncio.run(cp_surface_routes.condition_decisions(None, None))

    assert baselines["ok"] is True
    assert baselines["baselines"][0]["mean"] == 0.31
    assert baselines["baselines"][0]["calculated_at"]
    assert experiments["ok"] is True
    assert experiments["experiments"][0]["control"] == "A"
    assert experiments["experiments"][0]["variant"] == "B"
    assert decisions["ok"] is True
    assert decisions["decisions"][0]["state"] == "observe"
    assert decisions["decisions"][0]["created_at"]


def test_condition_records_without_workspace_brand_are_rejected(monkeypatch):
    engine = SimpleNamespace(
        _experiments={
            "experiment-unknown": {
                "id": "experiment-unknown",
                "name": "Unscoped experiment",
                "status": "running",
            }
        }
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)
    allow_workspace(monkeypatch)

    result = asyncio.run(cp_surface_routes.condition_experiments(None, None))

    assert result["ok"] is False
    assert result["source"] == "error"
    assert result["error"] == "ValueError"


def test_workspace_brand_resolution_checks_ownership_and_uses_repository(monkeypatch):
    import api.dependencies
    import api.v1_helpers

    calls = []

    class FakeRepository:
        def find_by_workspace(self, workspace_id):
            calls.append(("find_by_workspace", workspace_id))
            return [SimpleNamespace(id="brand-1"), SimpleNamespace(id="brand-2")]

    class FakeContainer:
        def resolve(self, repository_type):
            return FakeRepository()

    async def allow_ownership(resource_type, workspace_id, request, container):
        calls.append(("ownership", resource_type, workspace_id))

    async def run_sync(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(cp_surface_routes, "_workspace_id", lambda request: "workspace-1")
    monkeypatch.setattr(api.dependencies, "require_workspace_ownership", allow_ownership)
    monkeypatch.setattr(api.v1_helpers, "_async_wrap", run_sync)

    brand_ids = asyncio.run(cp_surface_routes._workspace_brand_ids(object(), FakeContainer()))

    assert brand_ids == {"brand-1", "brand-2"}
    assert ("ownership", "workspace", "workspace-1") in calls
    assert ("find_by_workspace", "workspace-1") in calls
