from types import SimpleNamespace
import pytest

from services.kdo_engineering_phase16_11_planner import (
    EngineeringPlannerError, make_engineering_plan_generator,
)

TASK = {"task_id": "task-1", "objective": "Add a secure API endpoint",
        "acceptance_criteria": ["Reject unauthorized users", "Tests cover allowed and denied requests"]}


def assignments():
    return [
        {"task_id": "security-1", "role": "security_engineer", "objective": "Threat model the endpoint",
         "acceptance_criteria": ["Threats and mitigations documented"], "depends_on": [],
         "deliverables": ["threat-model"], "policy_refs": ["security-policy-v1"],
         "requested_capabilities": ["inspect_auth_boundary"]},
        {"task_id": "backend-1", "role": "backend_engineer", "objective": "Implement the endpoint",
         "acceptance_criteria": ["Endpoint enforces authorization"], "depends_on": ["security-1"],
         "deliverables": ["patch"], "policy_refs": ["backend-policy-v1"],
         "requested_capabilities": ["read_task_workspace", "propose_patch"]},
        {"task_id": "tests-1", "role": "test_engineer", "objective": "Test allow and deny paths",
         "acceptance_criteria": ["Both paths have test coverage"], "depends_on": ["backend-1"],
         "deliverables": ["test-report"], "policy_refs": [],
         "requested_capabilities": ["run_scoped_tests"]},
        {"task_id": "review-1", "role": "independent_reviewer", "objective": "Review implementation and evidence",
         "acceptance_criteria": ["Review covers security and test evidence"], "depends_on": ["tests-1"],
         "deliverables": ["review-report"], "policy_refs": [],
         "requested_capabilities": ["inspect_diff", "inspect_test_evidence"]},
    ]


class Gateway:
    def __init__(self, data, ok=True):
        self.result = SimpleNamespace(ok=ok, data=data, provider="test-provider",
                                      model="test-model", request_id="req-1")
        self.calls = []

    def complete_structured(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.result


def good_data(items=None):
    return {"status": "succeeded", "summary": "A scoped specialist plan",
            "evidence": {"steps": ["Inspect auth boundary", "Implement endpoint",
                                   "Test allowed and denied requests", "Independently review evidence"],
                         "assignments": assignments() if items is None else items}}


def test_planner_returns_validated_specialist_assignments_without_authority():
    gw = Gateway(good_data())
    proposal = make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")
    assert len(proposal["steps"]) == 4
    assert [x["role"] for x in proposal["assignments"]] == [
        "security_engineer", "backend_engineer", "test_engineer", "independent_reviewer"]
    assert proposal["assignments"][1]["requested_capabilities"] == ["read_task_workspace", "propose_patch"]
    assert proposal["approval_required"] is True
    assert proposal["execution_started"] is False
    assert proposal["release_authorized"] is False
    assert gw.calls[0][1]["workspace_id"] == "workspace-1"
    assert gw.calls[0][1]["caller"] == "code_intelligence"
    assert "NOT runtime permissions or authorization" in gw.calls[0][0]


def test_planner_rejects_failed_gateway_response():
    gw = Gateway({}, ok=False)
    with pytest.raises(EngineeringPlannerError, match="llm_planning_unavailable"):
        make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")


def test_planner_rejects_too_few_or_invalid_steps():
    gw = Gateway({"status": "succeeded", "summary": "bad",
                  "evidence": {"steps": ["only one"], "assignments": assignments()}})
    with pytest.raises(EngineeringPlannerError, match="planner_output_steps_invalid"):
        make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")


def test_planner_requires_acceptance_criteria():
    gw = Gateway({})
    with pytest.raises(EngineeringPlannerError, match="acceptance_criteria_required"):
        make_engineering_plan_generator(gw)(task={**TASK, "acceptance_criteria": []},
                                             workspace_id="workspace-1")


@pytest.mark.parametrize("mutator,error", [
    (lambda xs: [*xs, dict(xs[0])], "duplicate_assignment_id"),
    (lambda xs: [{**xs[0], "role": "root_admin"}, *xs[1:]], "unknown_role"),
    (lambda xs: [{**xs[0], "depends_on": ["missing"]}, *xs[1:]], "dependencies_invalid"),
    (lambda xs: [{**xs[0], "depends_on": ["backend-1"]}, *xs[1:]], "dependency_cycle"),
    (lambda xs: [{**xs[0], "acceptance_criteria": []}, *xs[1:]], "criteria_invalid"),
])
def test_planner_rejects_invalid_specialist_assignment_graph(mutator, error):
    gw = Gateway(good_data(mutator(assignments())))
    with pytest.raises(EngineeringPlannerError, match=error):
        make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")


def test_planner_retries_once_when_model_returns_invalid_assignment_graph():
    class SequenceGateway:
        def __init__(self):
            self.calls = []
            self.results = [
                SimpleNamespace(ok=True, data=good_data(assignments()[:2]),
                                provider="test-provider", model="test-model", request_id="req-bad"),
                SimpleNamespace(ok=True, data=good_data(),
                                provider="test-provider", model="test-model", request_id="req-good"),
            ]

        def complete_structured(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return self.results.pop(0)

    gw = SequenceGateway()
    proposal = make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")
    assert len(gw.calls) == 2
    assert "REPAIR REQUIRED" in gw.calls[1][0]
    assert proposal["planner"]["request_id"] == "req-good"
    assert proposal["approval_required"] is True


def test_planner_retries_once_after_structured_json_parse_failure():
    class SequenceGateway:
        def __init__(self):
            self.calls = []
            self.results = [
                SimpleNamespace(ok=False, data=None, text='{"status":',
                                reason="structured_parse_failed", provider="gemini",
                                model="gemini-2.5-flash", request_id="req-bad"),
                SimpleNamespace(ok=True, data=good_data(),
                                provider="gemini", model="gemini-2.5-flash", request_id="req-good"),
            ]

        def complete_structured(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return self.results.pop(0)

    gw = SequenceGateway()
    proposal = make_engineering_plan_generator(gw)(task=TASK, workspace_id="workspace-1")
    assert len(gw.calls) == 2
    assert "CANDIDATE RESPONSE" in gw.calls[1][0]
    assert proposal["planner"]["request_id"] == "req-good"
