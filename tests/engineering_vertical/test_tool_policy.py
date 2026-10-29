import pytest
from services.kdo_engineering_tool_policy import EngineeringToolPolicy, ToolPermissionDenied

TASK = {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "task_id": "task-a"}


def test_policy_fails_closed_without_authorizer():
    with pytest.raises(ToolPermissionDenied, match="authorizer_not_configured"):
        EngineeringToolPolicy().require(action="run_tests", task=TASK, agent_id="a", tool_name="pytest", decision_id="d1")


def test_policy_requires_literal_true_authorization():
    policy = EngineeringToolPolicy(lambda action, task: 1)
    with pytest.raises(ToolPermissionDenied, match="not_authorized"):
        policy.require(action="run_tests", task=TASK, agent_id="a", tool_name="pytest", decision_id="d1")


def test_policy_requires_authoritative_decision_reference():
    policy = EngineeringToolPolicy(lambda action, task: True)
    with pytest.raises(ToolPermissionDenied, match="decision_id_required"):
        policy.require(action="run_tests", task=TASK, agent_id="a", tool_name="pytest")


def test_policy_returns_scoped_grant_only_after_all_gates():
    policy = EngineeringToolPolicy(lambda action, task: True)
    grant = policy.require(action="run_tests", task=TASK, agent_id="test-agent", tool_name="pytest", decision_id="council-decision-42")
    assert (grant.tenant_id, grant.workspace_id, grant.task_id) == ("tenant-a", "workspace-a", "task-a")
    assert grant.decision_id == "council-decision-42"
