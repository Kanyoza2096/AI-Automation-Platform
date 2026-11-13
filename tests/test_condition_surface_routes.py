import asyncio
from types import SimpleNamespace

import services.condition_engine as condition_engine
from services.condition_engine.models import Baseline, Decision, DecisionState, Experiment
from routes import cp_surface_routes


def test_condition_baselines_serialize_keyed_dataclass_like_records(monkeypatch):
    engine = SimpleNamespace(
        _baselines={
            ("brand-1", "engagement_rate"): {
                "metric": "engagement_rate",
                "mean": 0.24,
                "sample_count": 12,
            }
        }
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)

    result = asyncio.run(cp_surface_routes.condition_baselines(None))

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
                "name": "Landing page test",
                "control": "A",
                "variant": "B",
                "status": "running",
            }
        }
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)

    result = asyncio.run(cp_surface_routes.condition_experiments(None))

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["experiments"][0]["id"] == "experiment-1"
    assert result["experiments"][0]["control"] == "A"


def test_condition_decisions_uses_actual_decision_store(monkeypatch):
    engine = SimpleNamespace(
        _decisions={
            "decision-1": {
                "id": "decision-1",
                "reason": "Engagement dropped",
                "recommended_action": "Review posting cadence",
                "status": "proposed",
            }
        },
        get_status=lambda: {"decisions": 1},
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)

    result = asyncio.run(cp_surface_routes.condition_decisions())

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["decisions"][0]["id"] == "decision-1"
    assert result["decisions"][0]["reason"] == "Engagement dropped"
    assert result["status"]["decisions"] == 1


def test_valid_empty_condition_stores_remain_valid_empty_collections(monkeypatch):
    engine = SimpleNamespace(_baselines={}, _experiments={}, _decisions={})
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)

    baselines = asyncio.run(cp_surface_routes.condition_baselines(None))
    experiments = asyncio.run(cp_surface_routes.condition_experiments())
    decisions = asyncio.run(cp_surface_routes.condition_decisions())

    assert baselines["ok"] is True and baselines["baselines"] == []
    assert experiments["ok"] is True and experiments["experiments"] == []
    assert decisions["ok"] is True and decisions["decisions"] == []


def test_missing_condition_provider_is_not_reported_as_successful_empty(monkeypatch):
    monkeypatch.setattr(condition_engine, "autonomous_engine", SimpleNamespace())

    baselines = asyncio.run(cp_surface_routes.condition_baselines(None))
    experiments = asyncio.run(cp_surface_routes.condition_experiments())
    decisions = asyncio.run(cp_surface_routes.condition_decisions())

    assert baselines["ok"] is False and baselines["source"] == "error"
    assert experiments["ok"] is False and experiments["source"] == "error"
    assert decisions["ok"] is False and decisions["source"] == "error"


def test_condition_provider_exception_is_not_reported_as_successful_empty(monkeypatch):
    class BrokenConditionEngine:
        def get_baselines(self):
            raise RuntimeError("internal provider details must not be exposed")

    monkeypatch.setattr(condition_engine, "autonomous_engine", BrokenConditionEngine())

    result = asyncio.run(cp_surface_routes.condition_baselines(None))

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
        get_status=lambda: {"decisions": 1},
    )
    monkeypatch.setattr(condition_engine, "autonomous_engine", engine)

    baselines = asyncio.run(cp_surface_routes.condition_baselines(None))
    experiments = asyncio.run(cp_surface_routes.condition_experiments())
    decisions = asyncio.run(cp_surface_routes.condition_decisions())

    assert baselines["ok"] is True
    assert baselines["baselines"][0]["mean"] == 0.31
    assert baselines["baselines"][0]["calculated_at"]
    assert experiments["ok"] is True
    assert experiments["experiments"][0]["control"] == "A"
    assert experiments["experiments"][0]["variant"] == "B"
    assert decisions["ok"] is True
    assert decisions["decisions"][0]["state"] == "observe"
    assert decisions["decisions"][0]["created_at"]
