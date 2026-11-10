from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_health_service_is_built_from_local_startup_runtime_not_public_app_state():
    main = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "app.state.tenant_facebook_provenance_runtime" not in main
    assert "runtime=provenance_result.runtime" in main
    assert "app.state.tenant_facebook_health_service" in main
    assert "app.state.tenant_facebook_provenance_enabled = True" in main
    assert "app.state.tenant_facebook_provenance_key_id" in main


def test_tenant_health_route_accepts_only_the_composed_observe_service():
    route = (ROOT / "routes" / "tenant_health_observation_routes.py").read_text(encoding="utf-8")

    assert 'getattr(request.app.state, "tenant_facebook_health_service", None)' in route
    assert 'getattr(identity, "workspace_id", "")' in route
    assert 'permission_check("write")' in route
    assert "service.observe(" in route
    assert "workspace_id=" not in route.split("async def observe_tenant_facebook_health", 1)[1].split("):", 1)[0]
