from services.kdo_engineering_agent_team import AgentTeamError, coordinate_engineering_team

def _agents(*, reviewer_id="reviewer-1", review_status="approved", tests_passed=True):
    def agent(agent_id, evidence=None):
        return lambda assignment: {"agent_id": agent_id, "status": "succeeded",
            "summary": assignment.role + " completed", "evidence": evidence or {}}
    return {"planner": agent("planner-1"),
        "implementer": agent("implementer-1", {"patch_digest": "sha256:test"}),
        "test_engineer": agent("tester-1", {"tests_passed": tests_passed, "test_run_id": "test-1"}),
        "reviewer": agent(reviewer_id, {"review_status": review_status, "review_id": "review-1"})}

def test_team_requires_all_roles():
    try:
        coordinate_engineering_team(task_id="t", objective="o", acceptance_criteria=("a",), agents={})
    except AgentTeamError as exc:
        assert "missing_agent_roles" in str(exc)
    else:
        raise AssertionError("missing roles must fail closed")

def test_independent_review_and_test_evidence_reach_awaiting_authorization():
    result = coordinate_engineering_team(task_id="t", objective="o", acceptance_criteria=("a",), agents=_agents())
    assert result.status == "awaiting_authorization" and result.release_authorized is False
    assert len(result.evidence_fingerprint) == 64

def test_implementer_cannot_self_review():
    result = coordinate_engineering_team(task_id="t", objective="o", acceptance_criteria=("a",), agents=_agents(reviewer_id="implementer-1"))
    assert result.status == "blocked" and result.release_authorized is False

def test_failed_tests_block_team_report():
    result = coordinate_engineering_team(task_id="t", objective="o", acceptance_criteria=("a",), agents=_agents(tests_passed=False))
    assert result.status == "blocked"

def test_failed_planner_stops_downstream_agents():
    agents = _agents()
    agents["planner"] = lambda assignment: {"agent_id": "p", "status": "failed", "summary": "planning failed", "evidence": {}}
    result = coordinate_engineering_team(task_id="t", objective="o", acceptance_criteria=("a",), agents=agents)
    assert result.status == "blocked" and [x.role for x in result.contributions] == ["planner"]


def test_planned_assignments_are_dispatched_with_contract_and_dependency_evidence():
    from services.kdo_engineering_agent_team import coordinate_planned_assignments
    seen = []
    def adapter(assignment):
        seen.append(assignment)
        return {"agent_id": assignment.role + "-host", "status": "succeeded",
                "summary": "completed", "evidence": {"proposal": True}}
    assignments = [
        {"task_id": "a", "role": "security_engineer", "objective": "Threat model",
         "acceptance_criteria": ["Threats documented"], "depends_on": [],
         "deliverables": ["model"], "policy_refs": ["security-v1"],
         "requested_capabilities": ["inspect_auth"]},
        {"task_id": "b", "role": "backend_engineer", "objective": "Implement API",
         "acceptance_criteria": ["Auth enforced"], "depends_on": ["a"],
         "deliverables": ["patch"], "policy_refs": [],
         "requested_capabilities": ["propose_patch"]},
    ]
    result = coordinate_planned_assignments(assignments=assignments,
        agents={"security_engineer": adapter, "backend_engineer": adapter})
    assert result["status"] == "proposal_complete"
    assert result["release_authorized"] is False
    assert seen[1].depends_on == ("a",)
    assert seen[1].context["requested_capabilities"] == ["propose_patch"]
    assert seen[1].context["prior_contributions"]["a"]["status"] == "succeeded"


def test_planned_assignments_fail_closed_when_specialist_missing_or_dependency_fails():
    from services.kdo_engineering_agent_team import coordinate_planned_assignments
    assignments = [
        {"task_id": "a", "role": "security_engineer", "objective": "Threat model",
         "acceptance_criteria": ["Threats documented"], "depends_on": [],
         "requested_capabilities": []},
        {"task_id": "b", "role": "backend_engineer", "objective": "Implement API",
         "acceptance_criteria": ["Auth enforced"], "depends_on": ["a"],
         "requested_capabilities": ["propose_patch"]},
    ]
    result = coordinate_planned_assignments(assignments=assignments, agents={})
    assert result["status"] == "blocked"
    assert [x["status"] for x in result["contributions"]] == ["failed", "blocked"]
    assert result["execution_started"] is False


def test_planned_assignment_dependency_cycle_is_rejected():
    from services.kdo_engineering_agent_team import coordinate_planned_assignments
    assignments = [
        {"task_id": "a", "role": "security_engineer", "objective": "Threat model",
         "acceptance_criteria": ["Threats documented"], "depends_on": ["b"]},
        {"task_id": "b", "role": "backend_engineer", "objective": "Implement API",
         "acceptance_criteria": ["Auth enforced"], "depends_on": ["a"]},
    ]
    with pytest.raises(AgentTeamError, match="dependency_cycle"):
        coordinate_planned_assignments(assignments=assignments, agents={})
