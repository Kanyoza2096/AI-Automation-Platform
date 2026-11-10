from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.director import Director
from services.director.planning import StrategicPlanningError, make_llm_strategic_planner


def objective():
    return {
        "objective_id": "objective-improve-content-quality",
        "title": "Improve content quality",
        "description": "Improve measured quality without bypassing brand publishing ownership.",
        "success_criteria": ["Quality score improves based on verified observations."],
        "constraints": ["Do not publish content.", "Do not deploy production changes."],
    }


def generated_plan():
    return {
        "summary": "Review quality evidence, then recommend schedule adjustments.",
        "steps": [
            {
                "step_id": "review",
                "capability": "review_content_quality",
                "sector": "content",
                "objective": "Review the evidence behind the quality gap.",
                "depends_on": [],
                "acceptance_criteria": ["A quality review is supported by current evidence."],
                "deliverables": ["Evidence-linked quality assessment"],
                "rationale": "A diagnosis should precede schedule changes.",
            },
            {
                "step_id": "schedule",
                "capability": "optimize_content_schedule",
                "sector": "content",
                "objective": "Recommend a schedule optimization based on the review.",
                "depends_on": ["review"],
                "acceptance_criteria": ["A recommendation is supported by the review."],
                "deliverables": ["Schedule recommendation"],
                "rationale": "Schedule changes are owned by the brand scheduler.",
            },
        ],
    }


def test_director_plans_objective_with_bounded_context_and_no_execution():
    calls = []
    director = Director(supabase_client=None, strategic_planner=lambda **kwargs: calls.append(kwargs) or generated_plan())
    before_tasks = set(director.tasks)

    plan = director.plan_objective(objective())

    assert len(calls) == 1
    assert calls[0]["context"]["schema_version"] == "kdo.director.strategic-context.v1"
    assert calls[0]["context"]["authority"] == "none"
    assert plan["schema_version"] == "kdo.director.strategic-plan.v1"
    assert plan["status"] == "proposed"
    assert plan["step_count"] == 2
    assert plan["dependency_edge_count"] == 1
    assert plan["steps"][1]["depends_on"] == ["review"]
    assert all(step["authority"] == "none" for step in plan["steps"])
    assert all(step["capability_readiness"]["disposition"] == "advisory_only_capability_not_ready" for step in plan["steps"])
    assert plan["advisory_only"] is True
    assert plan["execution_started"] is False
    assert plan["tasks_created"] is False
    assert set(director.tasks) == before_tasks
    assert director.get_strategic_plan(plan["plan_id"])["plan_id"] == plan["plan_id"]


def test_director_does_not_run_planner_automatically_or_without_configuration():
    director = Director(supabase_client=None)
    result = director.plan_objective(objective())

    assert result["status"] == "unavailable"
    assert result["reason"] == "strategic_planner_not_configured"
    assert result["authority"] == "none"
    assert result["execution_started"] is False


@pytest.mark.parametrize(
    "mutate, error",
    [
        (lambda data: data["steps"][0].update(capability="unknown_capability"), "plan_references_unknown_capability"),
        (lambda data: data["steps"][0].update(sector="security"), "plan_capability_sector_mismatch"),
        (lambda data: data["steps"][0].update(depends_on=["missing"]), "invalid_plan_dependency"),
        (lambda data: data["steps"][0].update(depends_on=["review"]), "invalid_plan_dependency"),
        (lambda data: data["steps"][0].update(params={"secret": "must-not-pass"}), "plan_step_fields_invalid"),
    ],
)
def test_director_rejects_invalid_or_unsafe_planner_output(mutate, error):
    candidate = deepcopy(generated_plan())
    mutate(candidate)
    director = Director(supabase_client=None, strategic_planner=lambda **kwargs: candidate)

    with pytest.raises(StrategicPlanningError, match=error):
        director.plan_objective(objective())


def test_director_rejects_dependency_cycles():
    candidate = deepcopy(generated_plan())
    candidate["steps"][0]["depends_on"] = ["schedule"]
    director = Director(supabase_client=None, strategic_planner=lambda **kwargs: candidate)

    with pytest.raises(StrategicPlanningError, match="plan_dependency_cycle"):
        director.plan_objective(objective())


def test_director_rejects_objective_without_measurable_success_criteria():
    director = Director(supabase_client=None, strategic_planner=lambda **kwargs: generated_plan())
    invalid = objective()
    invalid["success_criteria"] = []

    with pytest.raises(StrategicPlanningError, match="success_criteria_invalid"):
        director.plan_objective(invalid)


def test_director_rejects_planner_output_with_execution_authority_fields():
    candidate = generated_plan()
    candidate["execution_started"] = True
    director = Director(supabase_client=None, strategic_planner=lambda **kwargs: candidate)

    with pytest.raises(StrategicPlanningError, match="planner_output_invalid"):
        director.plan_objective(objective())


def test_llm_adapter_uses_structured_director_gateway_and_redacts_execution_payloads():
    class FakeGateway:
        def __init__(self):
            self.calls = []

        def complete_structured(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return SimpleNamespace(ok=True, data=generated_plan())

    gateway = FakeGateway()
    adapter = make_llm_strategic_planner(gateway)
    director = Director(supabase_client=None)
    context = director.strategic_context()
    context["evidence"]["engine_signals"]["accepted"] = [{
        "capability": "review_content_quality",
        "description": "Review evidence",
        "params": {"api_key": "do-not-send-this-secret"},
    }]
    context["missions"]["recent_tasks"] = [{
        "task_id": "task-1",
        "capability": "review_content_quality",
        "status": "planned",
        "params": {"access_token": "also-do-not-send"},
    }]

    result = adapter(objective=objective(), context=context)

    assert result == generated_plan()
    assert len(gateway.calls) == 1
    prompt, kwargs = gateway.calls[0]
    assert kwargs["caller"] == "director"
    assert kwargs["workspace_id"] == "platform"
    assert kwargs["required_keys"] == ["summary", "steps"]
    assert kwargs["temperature"] == 0.1
    assert "do-not-send-this-secret" not in prompt
    assert "also-do-not-send" not in prompt
    assert '"params"' not in prompt
    assert "brand scheduler" in prompt.lower()


def test_llm_adapter_fails_closed_when_gateway_is_unavailable():
    class UnavailableGateway:
        def complete_structured(self, prompt, **kwargs):
            return SimpleNamespace(ok=False, reason="provider_not_configured")

    director = Director(supabase_client=None)
    adapter = make_llm_strategic_planner(UnavailableGateway())

    with pytest.raises(StrategicPlanningError, match="llm_strategic_planner_unavailable"):
        adapter(objective=objective(), context=director.strategic_context())


def test_llm_adapter_requires_structured_gateway():
    with pytest.raises(StrategicPlanningError, match="configured_llm_gateway_required"):
        make_llm_strategic_planner(None)
