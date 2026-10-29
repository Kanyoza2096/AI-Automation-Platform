import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerSelectionRegistry, WorkerAvailability, SelectionBlock


def setup():
    ids = AgentIdentityRegistry("ws-1")
    for aid, cls in (("a1", "fast"), ("a2", "steady"), ("a3", "suspended")):
        ids.register(agent_id=aid, name=aid, purpose="ops", agent_class=cls, declared_capabilities=("crm.read",))
        ids.transition(agent_id=aid, status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    for aid in ("a1", "a2", "a3"):
        perms.grant(permission_id=f"p-{aid}", agent_id=aid, capability="crm.read", scope={"workspace_id": "ws-1"})
    cap = WorkerCapacityRegistry("ws-1", ids)
    cap.register(agent_id="a1", max_concurrent_tasks=2, active_tasks=0, load_score=.1, autonomy_level=2)
    cap.register(agent_id="a2", max_concurrent_tasks=2, active_tasks=1, load_score=.7, autonomy_level=2)
    cap.register(agent_id="a3", availability=WorkerAvailability.UNAVAILABLE, autonomy_level=2)
    budgets = AgentBudgetRegistry("ws-1", ids)
    budgets.create(budget_id="b1", agent_id="a1", max_actions=10)
    budgets.create(budget_id="b2", agent_id="a2", max_actions=10)
    return ids, perms, cap, budgets, WorkerSelectionRegistry("ws-1", ids, perms, cap, budgets)


def test_selection_prefers_capacity_and_never_grants_authority():
    _, _, _, budgets, reg = setup()
    s = reg.select(selection_id="s1", task_id="t1", capability="crm.read", requested_scope={"workspace_id":"ws-1"}, budget_ids={"a1":"b1","a2":"b2"})
    assert s.selected_agent_id == "a1"
    assert s.advisory_only and not s.authority_granted and not s.execution_requested


def test_capability_permission_and_scope_are_required():
    ids, perms, cap, budgets, reg = setup()
    perms.grant(permission_id="wrong", agent_id="a1", capability="crm.read", scope={"workspace_id":"other"})
    s = reg.select(selection_id="s2", task_id="t2", capability="crm.write", requested_scope={"workspace_id":"ws-1"}, candidate_agent_ids=("a1",), budget_ids={"a1":"b1"})
    assert s.selected_agent_id is None


def test_unavailable_or_safety_held_workers_are_excluded():
    ids, perms, cap, budgets, reg = setup()
    cap.update_load("a1", active_tasks=2, load_score=1.0, availability=WorkerAvailability.BUSY)
    cap.update_load("a2", active_tasks=0, load_score=.1)
    state = cap.get("a2")
    object.__setattr__(state, "safety_posture", "hold")
    with pytest.raises(ValueError, match=SelectionBlock.CAPACITY_TAMPERED.value):
        cap.assert_integrity(state)


def test_budget_exhaustion_excludes_candidate():
    _, _, _, budgets, reg = setup()
    budgets.record_usage("b1", actions=10)
    s = reg.select(selection_id="s3", task_id="t3", capability="crm.read", requested_scope={"workspace_id":"ws-1"}, candidate_agent_ids=("a1",), budget_ids={"a1":"b1"})
    assert s.selected_agent_id is None


def test_autonomy_floor_and_deterministic_tie_break():
    _, _, cap, budgets, reg = setup()
    cap.update_load("a1", active_tasks=0, load_score=.2)
    cap.update_load("a2", active_tasks=0, load_score=.2)
    s = reg.select(selection_id="s4", task_id="t4", capability="crm.read", requested_scope={"workspace_id":"ws-1"}, minimum_autonomy_level=2, budget_ids={"a1":"b1","a2":"b2"})
    assert s.selected_agent_id == "a1"
    assert s.candidates[0].agent_id == "a1"


def test_tamper_and_cross_workspace_are_blocked():
    _, _, cap, _, reg = setup()
    s = reg.select(selection_id="s5", task_id="t5", capability="crm.read", requested_scope={"workspace_id":"ws-1"})
    object.__setattr__(s, "task_id", "tampered")
    with pytest.raises(ValueError, match=SelectionBlock.SELECTION_TAMPERED.value):
        reg.assert_integrity(s)
    object.__setattr__(s, "task_id", "t5")
    object.__setattr__(s, "workspace_id", "ws-2")
    with pytest.raises(ValueError, match=SelectionBlock.TENANT_MISMATCH.value):
        reg.assert_integrity(s)


def test_no_eligible_worker_is_explicit():
    _, _, cap, budgets, reg = setup()
    cap.update_load("a1", active_tasks=2, load_score=1.0, availability=WorkerAvailability.BUSY)
    cap.update_load("a2", active_tasks=2, load_score=1.0, availability=WorkerAvailability.BUSY)
    s = reg.select(selection_id="s6", task_id="t6", capability="crm.read", requested_scope={"workspace_id":"ws-1"})
    assert s.selected_agent_id is None
    assert reg.context("s6")["authority_granted"] is False
