from types import SimpleNamespace
import pytest
from services.kdo_engineering_phase16_11_planner import (
    EngineeringPlannerError, make_engineering_plan_generator,
)

TASK = {"task_id": "task-1", "objective": "Add a secure API endpoint",
        "acceptance_criteria": ["Reject unauthorized users", "Tests cover allowed and denied requests"]}

class Gateway:
    def __init__(self, result): self.result = result; self.calls = []
    def complete_structured(self, prompt, **kwargs): self.calls.append((prompt, kwargs)); return self.result

def result(data, ok=True):
    return SimpleNamespace(ok=ok, data=data, provider="test-provider", model="test-model", request_id="req-1")

def test_planner_returns_bounded_proposal_without_execution_authority():
    gw = Gateway(result({"status":"succeeded", "summary":"A scoped plan", "evidence":{"steps":["Inspect routes and auth dependencies", "Implement endpoint with workspace authorization", "Add allow/deny tests", "Run relevant test suite", "Have an independent reviewer inspect evidence"]}}))
    proposal = make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")
    assert len(proposal["steps"]) == 5
    assert proposal["approval_required"] is True
    assert proposal["execution_started"] is False
    assert proposal["release_authorized"] is False
    assert gw.calls[0][1]["workspace_id"] == "workspace-1"
    assert gw.calls[0][1]["caller"] == "code_intelligence"

def test_planner_rejects_failed_gateway_response():
    gw = Gateway(result({}, ok=False))
    with pytest.raises(EngineeringPlannerError, match="llm_planning_unavailable"):
        make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")

def test_planner_rejects_too_few_or_invalid_steps():
    gw = Gateway(result({"status":"succeeded", "summary":"bad", "evidence":{"steps":["only one"]}}))
    with pytest.raises(EngineeringPlannerError, match="planner_output_steps_invalid"):
        make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")

def test_planner_requires_acceptance_criteria():
    gw = Gateway(result({}))
    with pytest.raises(EngineeringPlannerError, match="acceptance_criteria_required"):
        make_engineering_plan_generator(gw)(task={**TASK, "acceptance_criteria": []}, workspace_id="workspace-1")
