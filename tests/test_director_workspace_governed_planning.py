import pytest

from services.director.workspace_planning import (
    WorkspacePlanningError,
    build_workspace_plan,
)


def evaluation():
    return {
        "schema_version": "kdo.director.website-evaluation.v1",
        "evaluation_id": "website_eval_123456789012345678901234",
        "workspace_id": "workspace-a",
        "integration_id": "integration-a",
        "event_id": "event-a",
        "context_hash": "a" * 64,
        "significance": "medium",
        "urgency": "normal",
        "action_warranted": True,
        "business_implications": ["Service information may need review."],
        "evidence_refs": ["website_context:" + "a" * 64, "website_event:event-a"],
        "recommended_objective": {
            "title": "Review service information",
            "description": "Assess whether the current service information is accurate.",
            "success_criteria": ["A verified assessment is recorded."],
            "constraints": ["Do not change website content."],
        },
        "status": "evaluated",
        "record_scope": "workspace",
        "authority": "none",
        "advisory_only": True,
        "tasks_created": False,
        "content_generation_started": False,
        "public_publishing_authorized": False,
        "deployment_authorized": False,
    }


def test_builds_deterministic_workspace_scoped_plan():
    source = evaluation()
    first = build_workspace_plan(source, workspace_id="workspace-a", requested_by="user-1")
    second = build_workspace_plan(source, workspace_id="workspace-a", requested_by="user-1")

    assert first["plan_id"] == second["plan_id"]
    assert first["workspace_id"] == "workspace-a"
    assert first["source"]["evaluation_id"] == source["evaluation_id"]
    assert first["status"] == "proposed"
    assert first["approval_status"] == "pending"
    assert first["approval_required"] is True
    assert first["authority"] == "none"
    assert first["advisory_only"] is True
    assert first["tasks_created"] is False
    assert first["content_generation_started"] is False
    assert first["public_publishing_authorized"] is False
    assert first["deployment_authorized"] is False


def test_role_identity_is_stable_and_task_spec_is_dynamic():
    plan = build_workspace_plan(evaluation(), workspace_id="workspace-a", requested_by="user-1")
    assert [step["role_id"] for step in plan["steps"]] == [
        "business_analyst", "evidence_verifier", "independent_reviewer"
    ]
    assert all(step["role_version"] == "1.0" for step in plan["steps"])
    assert all(step["task_spec"]["tool_permissions"] == [] for step in plan["steps"])
    assert all(step["status"] == "proposed" for step in plan["steps"])
    assert plan["steps"][0]["task_spec"]["project_context"]["workspace_id"] == "workspace-a"
    assert "Review service information" in plan["steps"][0]["task_spec"]["objective"]
    assert plan["steps"][2]["depends_on"] == ["step_01_analysis", "step_02_evidence"]


@pytest.mark.parametrize("change,error", [
    ({"workspace_id": "workspace-b"}, "evaluation_workspace_mismatch"),
    ({"action_warranted": False}, "evaluation_does_not_warrant_planning"),
    ({"authority": "auto"}, "evaluation_authority_contract_invalid"),
    ({"tasks_created": True}, "evaluation_authority_contract_invalid"),
    ({"content_generation_started": True}, "evaluation_authority_contract_invalid"),
    ({"public_publishing_authorized": True}, "evaluation_authority_contract_invalid"),
    ({"deployment_authorized": True}, "evaluation_authority_contract_invalid"),
    ({"status": "pending"}, "evaluation_not_completed_workspace_record"),
    ({"record_scope": "platform"}, "evaluation_not_completed_workspace_record"),
])
def test_rejects_untrusted_or_unauthorized_evaluation_fields(change, error):
    source = evaluation()
    source.update(change)
    with pytest.raises(WorkspacePlanningError, match=error):
        build_workspace_plan(source, workspace_id="workspace-a", requested_by="user-1")


def test_rejects_missing_objective_and_duplicate_evidence():
    source = evaluation()
    source["recommended_objective"] = None
    with pytest.raises(WorkspacePlanningError, match="evaluation_objective_missing"):
        build_workspace_plan(source, workspace_id="workspace-a", requested_by="user-1")

    source = evaluation()
    source["evidence_refs"] = [source["evidence_refs"][0]] * 2
    with pytest.raises(WorkspacePlanningError, match="evaluation_evidence_refs_not_unique"):
        build_workspace_plan(source, workspace_id="workspace-a", requested_by="user-1")
