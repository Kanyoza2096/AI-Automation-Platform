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
    assert {
        "debugging_engineer", "frontend_engineer", "backend_engineer", "database_engineer",
        "integration_engineer", "refactoring_engineer", "independent_reviewer",
    }.issubset(
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


def test_gateway_specialists_complete_test_repair_verify_handoff_without_release_authority():
    from services.kdo_engineering_gateway_agents import make_gateway_quality_registry_factory
    from services.kdo_engineering_phase16_7 import run_dynamic_quality_loop

    gateway = FakeGateway()
    gateway.results = [
        _result({"status": "succeeded", "summary": "Root cause is missing validation",
                 "evidence": {"root_cause": "input validation missing"}}),
        _result({"status": "succeeded", "summary": "Proposed a bounded repair",
                 "evidence": {"patch_text": "diff --git a/calculator.py b/calculator.py"}}),
        _result({"status": "succeeded", "summary": "Machine evidence satisfies verification",
                 "evidence": {"verification_status": "approved", "review_id": "forged"}}),
        _result({"status": "succeeded", "summary": "Frontend repair proposal",
                 "evidence": {"patch_text": "diff --git a/x b/x"}}),
    ]

    def complete_structured(prompt, **kwargs):
        gateway.calls.append((prompt, kwargs))
        return gateway.results.pop(0)

    gateway.complete_structured = complete_structured
    registry = make_gateway_quality_registry_factory(
        gateway=gateway, workspace_id="workspace-1",
    )(SimpleNamespace(task_id="task-1"), "/managed/task-1", ())

    test_results = iter([
        {"status": "failed", "exit_code": 1, "stdout": "division by zero test failed"},
        {"status": "passed", "exit_code": 0, "stdout": "4 passed"},
    ])
    result = run_dynamic_quality_loop(
        task={
            "task_id": "task-1", "tenant_id": "tenant-1", "workspace_id": "workspace-1",
            "objective": "Build calculator", "acceptance_criteria": ["Handle division by zero"],
        },
        registry=registry,
        test_runner=lambda _iteration: next(test_results),
        apply_patch=lambda _patch: {"status": "passed"},
        authorize=lambda _action, _task: True,
        repair_capabilities={"backend", "api", "services"},
        max_repair_attempts=1,
    )
    assert result.status == "awaiting_authorization"
    assert len(result.test_runs) == 2
    assert result.verification["status"] == "approved"
    assert result.verification["independent_of_repair_agent"] is True
    assert result.release_authorized is False
    assert len(gateway.calls) == 3


    frontend = registry.adapter("frontend_engineer")
    frontend_proposal = frontend(AgentAssignment(
        "t1", "frontend_engineer", "Propose a UI repair",
        {"acceptance_criteria": ["Fix visible error"], "workspace_id": "w1"}, (),
    ))
    assert frontend_proposal["evidence"]["patch_text"] == "diff --git a/x b/x"
    assert len(gateway.calls) == 4

 

def test_gateway_agent_maps_requested_capabilities_into_task_prompt():
    gateway = FakeGateway(_result({"status": "succeeded", "summary": "planned",
                                   "evidence": {"plan": []}}))
    agent = make_gateway_agent(gateway=gateway, role="planner", agent_id="planner-1",
                               workspace_id="w1")
    assignment = AgentAssignment("task-1", "planner", "Plan safely",
        {"acceptance_criteria": ["No production changes"],
         "requested_capabilities": ["read_task_workspace", "propose_patch"]})
    agent(assignment)
    prompt = gateway.calls[0][0]
    assert "read_task_workspace" in prompt
    assert "NOT permissions or authorization" in prompt
