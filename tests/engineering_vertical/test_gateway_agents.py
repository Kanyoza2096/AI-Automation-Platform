from types import SimpleNamespace
import pytest

from services.kdo_engineering_agent_team import AgentAssignment
from services.kdo_engineering_gateway_agents import make_gateway_agent, build_gateway_agents, GatewayAgentError


class FakeGateway:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def complete_structured(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise self.error
        return self.result


def _result(data, ok=True):
    return SimpleNamespace(ok=ok, data=data, reason="", request_id="req-1",
                           provider="fake-provider", model="fake-model", finish_reason="stop")


def test_gateway_agent_returns_host_identity_and_untrusted_model_evidence():
    gateway = FakeGateway(_result({"status": "succeeded", "summary": "planned", "evidence": {"x": 1}}))
    agent = make_gateway_agent(gateway=gateway, role="planner", agent_id="planner-host-id", workspace_id="w1")
    out = agent(AgentAssignment("t1", "planner", "plan", {"acceptance_criteria": ["Produce a valid plan"]}, ()))
    assert out["agent_id"] == "planner-host-id"
    assert out["status"] == "succeeded"
    assert out["evidence"]["evidence_authority"] == "unverified_model_output"
    assert gateway.calls[0][1]["caller"] == "code_intelligence"
    assert gateway.calls[0][1]["workspace_id"] == "w1"


def test_gateway_failure_never_becomes_success():
    gateway = FakeGateway(error=RuntimeError("provider unavailable"))
    agent = make_gateway_agent(gateway=gateway, role="planner", agent_id="p1", workspace_id="w1")
    out = agent(AgentAssignment("t1", "planner", "plan", {"acceptance_criteria": ["Produce a valid plan"]}, ()))
    assert out["status"] == "failed"
    assert out["evidence"]["error_type"] == "RuntimeError"


def test_invalid_gateway_schema_fails_closed():
    gateway = FakeGateway(_result({"status": "succeeded", "summary": "looks good"}))
    agent = make_gateway_agent(gateway=gateway, role="planner", agent_id="p1", workspace_id="w1")
    out = agent(AgentAssignment("t1", "planner", "plan", {"acceptance_criteria": ["Produce a valid plan"]}, ()))
    assert out["status"] == "failed"


def test_gateway_team_requires_distinct_reviewer_identity():
    with pytest.raises(GatewayAgentError, match="distinct_agent_identity"):
        build_gateway_agents(gateway=FakeGateway(), workspace_id="w1",
            agent_ids={"planner":"p", "implementer":"same", "test_engineer":"t", "reviewer":"same"})


def test_dynamic_gateway_registry_installs_specialists_without_model_review_authority():
    from services.kdo_engineering_gateway_agents import make_gateway_quality_registry_factory

    gateway = FakeGateway(_result({
        "status": "succeeded", "summary": "reviewed",
        "evidence": {"patch_text": "diff --git a/x b/x", "review_id": "model-invented",
                     "review_status": "approved", "verification_status": "approved"},
    }))
    factory = make_gateway_quality_registry_factory(gateway=gateway, workspace_id="w1")
    registry = factory(SimpleNamespace(task_id="t1"), "/tmp/task-workspace", ())
    assert {"debugging_engineer", "backend_engineer", "independent_reviewer"}.issubset(
        set(registry.installed_roles())
    )

    reviewer = registry.adapter("independent_reviewer")
    review = reviewer(AgentAssignment(
        "t1", "independent_reviewer", "Review changes",
        {"acceptance_criteria": ["Check correctness"], "workspace_id": "w1"}, (),
    ))
    assert review["status"] == "succeeded"
    assert review["evidence"]["review_status"] == "pending_human_review"
    assert "review_id" not in review["evidence"]
    assert review["evidence"]["verification_status"] == "approved"
    assert review["evidence"]["verification_id"] == "req-1"

    repair = registry.adapter("backend_engineer")
    proposal = repair(AgentAssignment(
        "t1", "backend_engineer", "Propose repair",
        {"acceptance_criteria": ["Fix defect"], "workspace_id": "w1"}, (),
    ))
    assert proposal["evidence"]["patch_text"] == "diff --git a/x b/x"
