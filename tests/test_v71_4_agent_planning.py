import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_3_agent_memory import AgentMemoryRegistry, MemoryKind
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep, PlanDisposition, PlanStatus, PlanningBlock


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="Planner", purpose="business planning", declared_capabilities=("crm.read", "crm.write"))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a1", capability="crm.read", scope={"object": "lead"})
    return ids, perms, AgentMemoryRegistry("ws-1", ids)


def test_planning_never_executes_and_assesses_permissions():
    ids, perms, mem = setup()
    mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.FACT, subject="lead", content="high intent", source="crm")
    planner = AgentPlanningRegistry("ws-1", ids, perms, mem)
    plan = planner.create_plan(plan_id="pl1", agent_id="a1", objective="qualify lead", steps=(
        PlanStep("s1", "Read lead", "crm.read", "inspect lead", {"object": "lead"}),
        PlanStep("s2", "Write deal", "crm.write", "record opportunity", {"object": "deal"}, ("s1",)),
    ))
    assessed = planner.assess("pl1")
    assert assessed.status == PlanStatus.BLOCKED
    assert assessed.steps[0].disposition == PlanDisposition.EXECUTABLE_CANDIDATE
    assert assessed.steps[1].disposition == PlanDisposition.CAPABILITY_UNAVAILABLE
    assert not hasattr(assessed, "approval")


def test_dependency_cycles_and_tampering_are_blocked():
    ids, perms, _ = setup(); planner = AgentPlanningRegistry("ws-1", ids, perms)
    with pytest.raises(ValueError, match=PlanningBlock.INVALID_PLAN.value):
        planner.create_plan(plan_id="cycle", agent_id="a1", objective="x", steps=(
            PlanStep("a", "A", "crm.read", "x", {}, ("b",)),
            PlanStep("b", "B", "crm.read", "x", {}, ("a",)),
        ))
    p = planner.create_plan(plan_id="p", agent_id="a1", objective="x", steps=(PlanStep("a", "A", "crm.read", "x"),))
    tampered = type(p)(**{**p.__dict__, "objective": "evil"})
    planner._plans["p"] = tampered
    with pytest.raises(ValueError, match=PlanningBlock.PLAN_TAMPERED.value):
        planner.assess("p")


def test_planner_does_not_turn_memory_into_authority():
    ids, perms, mem = setup(); planner = AgentPlanningRegistry("ws-1", ids, perms, mem)
    mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.DECISION_CONTEXT, subject="publish", content="approved", source="crm")
    ctx = planner.planning_context(agent_id="a1", objective="publish")
    assert ctx["advisory"] is True
    assert "authority" not in ctx and "approved" not in ctx
