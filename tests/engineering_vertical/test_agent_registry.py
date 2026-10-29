import pytest
from services.kdo_engineering_agent_registry import (
    AgentRegistry, AgentRegistryError, ROLE_CATALOGUE, plan_team,
)


def test_catalogue_has_27_specialist_roles():
    assert len(ROLE_CATALOGUE) == 27
    assert len({role.role_id for role in ROLE_CATALOGUE}) == 27


def test_task_router_selects_roles_but_does_not_claim_adapters_exist():
    result = plan_team("security")
    assert "security_engineer" in result["selected_roles"]
    assert "authorization_auditor" in result["selected_roles"]
    assert result["ready"] is False
    assert result["execution_authorized"] is False


def test_registered_adapter_is_explicitly_reported():
    registry = AgentRegistry()
    registry.register_adapter("planner", lambda assignment: {"status": "ok"})
    assert "planner" in registry.installed_roles()
    assert registry.adapter("planner") is not None


def test_registry_rejects_unknown_or_non_callable_adapter():
    registry = AgentRegistry()
    with pytest.raises(AgentRegistryError):
        registry.register_adapter("not_a_role", lambda _: {})
    with pytest.raises(AgentRegistryError):
        registry.register_adapter("planner", "not callable")


def test_router_rejects_unknown_task_type():
    with pytest.raises(AgentRegistryError):
        plan_team("magic")
