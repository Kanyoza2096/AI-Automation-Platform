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
