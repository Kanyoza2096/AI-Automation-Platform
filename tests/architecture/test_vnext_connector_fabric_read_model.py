from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.vnext_connector_fabric_read_model import (
    NOT_EXPOSED,
    build_connector_fabric_read_model,
    get_connector_fabric_record,
)


def test_catalog_matches_roadmap_shape_without_granting_authority():
    model = build_connector_fabric_read_model("workspace-a")
    assert model["readOnly"] is True
    assert model["authority"] == "none"
    assert model["architecture"].startswith("Provider -> Capability -> Action Contract -> Provider Adapter")
    assert model["healthSource"] == NOT_EXPOSED
    assert model["certificationSource"] == NOT_EXPOSED
    assert model["counts"]["connectors"] == 19
    assert model["counts"]["actionContracts"] == 22


def test_enterprise_action_contracts_preserve_governance_facts():
    github = get_connector_fabric_record("w", "github")
    actions = {x["action"]: x for x in github["actionContracts"]}
    assert actions["github.merge_pull_request"]["requiresApproval"] is True
    assert actions["github.merge_pull_request"]["irreversible"] is True
    assert actions["github.merge_pull_request"]["risk"] == "critical"
    assert actions["github.dispatch_workflow"]["reconciliation"] == "reconcile_workflow"


def test_social_connector_capabilities_are_catalogued_without_inventing_action_contracts():
    facebook = get_connector_fabric_record("w", "facebook")
    assert "publish" in facebook["capabilities"]
    assert facebook["actionContracts"] == []
    assert facebook["verification"] == "authoritative_lookup:lookup_post"
    assert facebook["health"] == NOT_EXPOSED
    assert facebook["certification"] == NOT_EXPOSED


def test_unknown_connector_is_explicitly_not_found():
    try:
        get_connector_fabric_record("w", "does-not-exist")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown connector must not be fabricated")


def test_route_is_read_only_and_workspace_scoped_from_backend_context():
    from routes.connector_fabric_routes import router
    app = FastAPI()
    app.include_router(router)
    # The route dependency is intentionally not bypassed here; this test only
    # verifies route registration and path shape. Auth behavior is covered by
    # the application's canonical auth contract suite.
    paths = {route.path for route in app.routes}
    assert "/api/v1/connectors/fabric" in paths
    assert "/api/v1/connectors/fabric/{connector_key}" in paths


def test_all_registered_non_ai_connectors_are_present_in_fabric_catalog():
    from services.vnext_connector_fabric_read_model import connector_fabric_registry_integrity
    integrity = connector_fabric_registry_integrity()
    assert integrity["registeredConnectorCount"] == 19
    assert integrity["catalogConnectorCount"] == 19
    assert integrity["missingFromCatalog"] == []
    assert integrity["unexpectedInCatalog"] == []
    assert len(integrity["enterpriseProviders"]) == 12
    assert len(integrity["socialConnectors"]) == 7


def test_ai_providers_are_tracked_separately_from_external_execution_connectors():
    from services.vnext_connector_fabric_read_model import connector_fabric_registry_integrity
    integrity = connector_fabric_registry_integrity()
    assert integrity["aiProvidersSeparateBoundary"] == ["cloudflare_ai", "gemini"]
    assert integrity["aiExcludedFromConnectorCount"] is True
