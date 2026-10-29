import asyncio


def test_organism_vitals_exposes_explicit_life_status():
    from core.organism import Organism, OrganismConfig

    org = Organism(OrganismConfig(workspace_id="ws-test"))
    before = org.vitals()
    assert before["life_status"] == "STOPPED"
    assert before["life_status_basis"] == "organism_runtime_stopped"

    asyncio.run(org.start())
    after = org.vitals()
    assert after["running"] is True
    assert after["life_status"] == "ALIVE"
    assert after["life_status_basis"] == "organism_runtime_started"
    asyncio.run(org.stop())


def test_system_health_uses_real_plugin_registry():
    from plugins.base import get_plugin_registry
    from core.reliability import HealthChecker

    registry = get_plugin_registry()
    checker = HealthChecker(registry)
    result = checker.check_all()
    assert isinstance(result, dict)


def test_system_health_route_returns_health_checker_observation():
    from routes.backend_contract_routes import system_health

    payload = asyncio.run(system_health(None))
    assert payload["ok"] is True
    assert "health_checker" in payload["services"]
    assert "results" in payload["services"]["health_checker"]


def test_health_checker_does_not_treat_missing_ok_as_healthy(monkeypatch):
    import asyncio
    from routes import backend_contract_routes as routes

    class Checker:
        def __init__(self, _registry):
            pass
        def check_all(self):
            return {"plugin-a": {"detail": "probe omitted explicit result"}}

    monkeypatch.setattr("core.reliability.HealthChecker", Checker)
    payload = asyncio.run(routes.system_health(None))
    report = payload["services"]["health_checker"]
    assert report["status"] == "unknown"
    assert report["unknown_checks"] == 1
    assert report["failed_checks"] == 0


def test_health_checker_marks_mixed_explicit_and_unknown_checks_partial(monkeypatch):
    import asyncio
    from routes import backend_contract_routes as routes

    class Checker:
        def __init__(self, _registry):
            pass
        def check_all(self):
            return {"plugin-a": {"ok": True}, "plugin-b": {"detail": "no boolean result"}}

    monkeypatch.setattr("core.reliability.HealthChecker", Checker)
    payload = asyncio.run(routes.system_health(None))
    report = payload["services"]["health_checker"]
    assert report["status"] == "partial"
    assert report["explicit_checks"] == 1
    assert report["unknown_checks"] == 1


def test_supervisor_evidence_reports_director_signal_coverage_separately():
    from types import SimpleNamespace
    from routes.backend_contract_routes import _supervisor_evidence

    director = SimpleNamespace(
        organism_state=lambda max_age_seconds: {"condition": "nominal", "observations": []},
        _engine_decision_signals=lambda max_age_seconds: {
            "contract": "metrics.director_signals/v1",
            "coverage": [{"observer": "engine-a", "has_signal_contract": False}],
            "coverage_summary": {"observer_count": 1, "contract_enabled_observers": 0},
            "accepted": [],
            "rejected": [{"observer": "engine-a", "reason": "observation_not_fresh"}],
        },
    )
    evidence = _supervisor_evidence(None, director)
    report = evidence["director_signal_contract"]
    assert report["status"] == "observed"
    assert report["coverage_summary"]["contract_enabled_observers"] == 0
    assert report["rejection_reasons"] == {"observation_not_fresh": 1}
    assert evidence["policy"].endswith("no health signals are merged")
