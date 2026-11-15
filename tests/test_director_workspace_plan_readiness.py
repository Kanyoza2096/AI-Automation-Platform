import copy

from services.director.workspace_plan_readiness import assess_workspace_plan_readiness


def plan():
    return {
        "schema_version": "kdo.director.workspace-plan.v1",
        "plan_id": "workspace_plan_123",
        "workspace_id": "workspace-a",
        "source": {
            "evaluation_id": "evaluation-1",
            "integration_id": "integration-1",
            "event_id": "event-1",
            "context_hash": "a" * 64,
            "evidence_refs": ["website_event:event-1"],
        },
        "steps": [
            {
                "step_id": "step_01",
                "role_id": "business_analyst",
                "role_version": "1.0",
                "role_identity": {"title": "Business Analyst"},
                "task_spec": {
                    "title": "Analyze",
                    "objective": "Analyze the verified objective.",
                    "success_criteria": ["Map evidence to criteria."],
                    "constraints": ["No external changes."],
                    "deliverables": ["Written findings."],
                    "tool_permissions": [],
                },
                "depends_on": [],
                "status": "proposed",
            },
            {
                "step_id": "step_02",
                "role_id": "evidence_verifier",
                "role_version": "1.0",
                "role_identity": {"title": "Evidence Verifier"},
                "task_spec": {
                    "title": "Verify",
                    "objective": "Verify the supplied evidence.",
                    "success_criteria": ["All claims are traceable."],
                    "constraints": ["Do not invent evidence."],
                    "deliverables": ["Evidence notes."],
                    "tool_permissions": [],
                },
                "depends_on": ["step_01"],
                "status": "proposed",
            },
        ],
        "status": "proposed",
        "approval_status": "pending",
        "approval_required": True,
        "requested_by": "requester-1",
        "authority": "none",
        "advisory_only": True,
        "tasks_created": False,
        "content_generation_started": False,
        "public_publishing_authorized": False,
        "deployment_authorized": False,
    }


def assess(document=None, **overrides):
    args = {
        "persisted_workspace_id": "workspace-a",
        "persisted_status": "proposed",
        "approval_status": "pending",
        "requested_by": "requester-1",
        "approved_by": None,
    }
    args.update(overrides)
    return assess_workspace_plan_readiness(document or plan(), **args)


def test_pending_plan_is_blocked_from_review_and_execution():
    report = assess()
    assert report["review_ready"] is False
    assert report["execution_ready"] is False
    assert report["readiness_status"] == "blocked"
    assert "independent_human_approval_pending" in report["blockers"]
    assert report["capabilities_granted"] == []
    assert report["tasks_created"] is False


def test_independently_approved_plan_is_reviewable_but_never_executable():
    document = plan()
    document["status"] = "approved"
    document["approval_status"] = "approved"
    report = assess(
        document,
        persisted_status="approved",
        approval_status="approved",
        approved_by="reviewer-2",
    )
    assert report["review_ready"] is True
    assert report["execution_ready"] is False
    assert report["readiness_status"] == "reviewable_non_executable"
    assert report["authority"] == "none"
    assert report["capabilities_granted"] == []
    assert "execution_capabilities_not_implemented" in report["warnings"]


def test_requester_cannot_be_the_approver():
    document = plan()
    document["status"] = "approved"
    document["approval_status"] = "approved"
    report = assess(
        document,
        persisted_status="approved",
        approval_status="approved",
        approved_by="requester-1",
    )
    assert "requester_cannot_approve_own_plan" in report["blockers"]


def test_workspace_scope_mismatch_blocks_readiness():
    report = assess(persisted_workspace_id="workspace-b")
    assert "workspace_scope_mismatch" in report["blockers"]
    assert report["review_ready"] is False


def test_nonempty_tool_permissions_are_rejected():
    document = plan()
    document["steps"][0]["task_spec"]["tool_permissions"] = ["browser"]
    report = assess(document)
    assert "step_1_tool_permissions_not_empty" in report["blockers"]


def test_missing_acceptance_criteria_and_broken_dependencies_are_rejected():
    document = plan()
    document["steps"][0]["task_spec"]["success_criteria"] = []
    document["steps"][1]["depends_on"] = ["missing-step"]
    report = assess(document)
    assert "step_1_success_criteria_missing" in report["blockers"]
    assert "step_2_dependency_reference_invalid" in report["blockers"]


def test_rejected_plan_is_never_review_ready():
    document = plan()
    document["status"] = "rejected"
    document["approval_status"] = "rejected"
    report = assess(
        document,
        persisted_status="rejected",
        approval_status="rejected",
        approved_by="reviewer-2",
    )
    assert "plan_rejected" in report["blockers"]
    assert report["execution_ready"] is False


def test_readiness_identifier_is_deterministic_and_scope_bound():
    first = assess()
    second = assess(copy.deepcopy(plan()))
    other_workspace = assess(persisted_workspace_id="workspace-b")
    assert first["readiness_id"] == second["readiness_id"]
    assert first["readiness_id"] != other_workspace["readiness_id"]
