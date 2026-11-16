"""Regression tests for truthful engine observation and safe discovery."""
from types import ModuleType

from services.director import Director
from services.director.analysis import Sector
from services.director.core import _make_observer, auto_discover_engines


def test_status_payload_without_health_field_remains_unknown():
    class Engine:
        def get_status(self):
            return {"available": True, "queue_depth": 0}

    assert _make_observer("engine", Engine())()["status"] == "unknown"


def test_health_check_without_status_remains_unknown():
    class Engine:
        def health_check(self):
            return {"connected": True}

    assert _make_observer("engine", Engine())()["status"] == "unknown"


def test_stats_only_engine_is_not_mislabeled_healthy():
    class Engine:
        def get_stats(self):
            return {"processed": 12}

    result = _make_observer("engine", Engine())()
    assert result["status"] == "unknown"
    assert result["health_contract"] == "stats_only"


def test_explicit_health_status_is_preserved():
    class Engine:
        def get_status(self):
            return {"status": "degraded", "available": True}

    assert _make_observer("engine", Engine())()["status"] == "degraded"


def test_discovery_does_not_invoke_arbitrary_registered_callable(monkeypatch):
    module_name = "test_director_callable_discovery_fixture"
    module = ModuleType(module_name)
    calls = []

    def request_handler():
        calls.append("called")
        return {"status": "healthy"}

    module.request_handler = request_handler
    monkeypatch.setitem(__import__("sys").modules, module_name, module)
    monkeypatch.setattr(
        "services.director.core.ENGINE_REGISTRY",
        [(module_name, "request_handler", "request_handler", Sector.CONTENT)],
    )

    director = Director(supabase_client=None)
    try:
        assert auto_discover_engines(director, container=object()) == 0
        assert calls == []
        assert "request_handler" not in director.observers
        report = director.status()["diagnostics"]["engine_discovery"]
        assert report["unsupported_export_count"] == 1
        assert report["unsupported_exports"][0]["reason"] == "live_instance_or_explicit_adapter_required"
    finally:
        director.observers.clear()


def test_discovery_report_exposes_missing_registered_export(monkeypatch):
    module_name = "test_director_missing_export_fixture"
    monkeypatch.setitem(__import__("sys").modules, module_name, ModuleType(module_name))
    monkeypatch.setattr(
        "services.director.core.ENGINE_REGISTRY",
        [(module_name, "expected_engine", "expected_engine", Sector.CONTENT)],
    )

    director = Director(supabase_client=None)
    assert auto_discover_engines(director, container=object()) == 0
    report = director.status()["diagnostics"]["engine_discovery"]
    assert report["missing_export_count"] == 1
    assert report["missing_exports"][0]["export"] == "expected_engine"


def test_discovery_report_exposes_import_failure(monkeypatch):
    monkeypatch.setattr(
        "services.director.core.ENGINE_REGISTRY",
        [("module_that_does_not_exist_for_director_test", "engine", "engine", Sector.CONTENT)],
    )

    director = Director(supabase_client=None)
    assert auto_discover_engines(director, container=object()) == 0
    report = director.status()["diagnostics"]["engine_discovery"]
    assert report["import_failure_count"] == 1
    assert report["import_failures"][0]["observer"] == "engine"


def test_ready_status_is_normalized_to_nominal():
    from services.director.analysis import FunctionObserver

    class Engine:
        def get_status(self):
            return {"status": "ready"}

    observation = FunctionObserver("engine", Sector.CONTENT, lambda: {
        "status": "ready", "health_contract": "get_status",
    }).observe()
    assert observation.status.value == "nominal"
    assert observation.contract_version == "kdo.observer.v1"
    assert observation.health_contract == "get_status"


def test_invalid_source_timestamp_is_not_accepted_as_fresh_evidence():
    from services.director.analysis import FunctionObserver

    observation = FunctionObserver("engine", Sector.CONTENT, lambda: {
        "status": "healthy", "observed_at": "not-a-timestamp",
    }).observe()
    assert observation.timestamp_valid is False


def test_unknown_observers_do_not_create_a_synthetic_health_score():
    from services.director import Director
    from services.director.models import Health, Observation

    director = Director(supabase_client=None)
    director.observations = {
        "unknown": Observation("unknown", Sector.CONTENT, Health.UNKNOWN),
    }
    result = director.health()
    assert result["status"] == "unknown"
    assert result["score"] is None
    assert result["known_observer_count"] == 0


def test_stale_health_observations_are_excluded_from_aggregate():
    from datetime import timedelta
    from services.director import Director
    from services.director.models import Health, Observation
    from services.director.utils import now

    director = Director(supabase_client=None)
    director.observations = {
        "stale": Observation("stale", Sector.CONTENT, Health.NOMINAL, timestamp=now() - timedelta(hours=1)),
    }
    result = director.health()
    assert result["status"] == "unknown"
    assert result["score"] is None
    assert result["stale_observer_count"] == 1
