import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_6_agent_budgets import AgentBudgetRegistry
from services.v71_9_worker_selection import WorkerCapacityRegistry, WorkerSelectionRegistry
from services.v71_28_worker_reputation_capability_evidence import WorkerReputationCapabilityRegistry
from services.v71_29_reputation_aware_worker_selection import ReputationAwareWorkerSelectionRegistry, ReputationSelectionBlock
from services.v71_35_worker_capability_qualification import QualificationStatus

class FakeQualification:
    workspace_id = "ws-1"
    def __init__(self, qualified): self.qualified = set(qualified)
    def get_for_worker(self, worker_id, capability):
        if worker_id in self.qualified:
            return type("Q", (), {"worker_id":worker_id,"capability":capability})()
        return None
    def assert_integrity(self, q): return None
    def is_currently_qualified(self, worker_id, capability): return worker_id in self.qualified

def setup():
    ids = AgentIdentityRegistry("ws-1")
    for aid in ("a1", "a2"):
        ids.register(agent_id=aid, name=aid, purpose="ops", agent_class="worker", declared_capabilities=("crm.read",))
        ids.transition(agent_id=aid, status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    for aid in ("a1", "a2"):
        perms.grant(permission_id=f"p-{aid}", agent_id=aid, capability="crm.read", scope={"workspace_id":"ws-1"})
    cap = WorkerCapacityRegistry("ws-1", ids)
    cap.register(agent_id="a1", max_concurrent_tasks=2, load_score=.1, autonomy_level=2)
    cap.register(agent_id="a2", max_concurrent_tasks=2, load_score=.2, autonomy_level=2)
    budgets = AgentBudgetRegistry("ws-1", ids)
    budgets.create(budget_id="b1", agent_id="a1", max_actions=10)
    budgets.create(budget_id="b2", agent_id="a2", max_actions=10)
    base = WorkerSelectionRegistry("ws-1", ids, perms, cap, budgets)
    # The reputation registry only needs the correct workspace for this gate.
    class R:
        workspace_id = "ws-1"
        def profile(self, *, worker_id, capability):
            return type("P", (), {"sample_count":0,"verified_sample_count":0,"average_capability_score":0.0,"average_performance_score":0.0,"can_authorize":False,"can_execute":False,"can_call_provider":False,"evidence_ids":()})()
    reputation = R()
    return base, reputation

def test_unqualified_worker_is_not_selectable():
    base, reputation = setup()
    selector = ReputationAwareWorkerSelectionRegistry("ws-1", base, reputation, qualification=FakeQualification(set()))
    result = selector.select(selection_id="s1", task_id="t1", capability="crm.read", requested_scope={"workspace_id":"ws-1"}, budget_ids={"a1":"b1","a2":"b2"})
    assert result.selected_agent_id is None

def test_qualified_worker_is_selectable():
    base, reputation = setup()
    selector = ReputationAwareWorkerSelectionRegistry("ws-1", base, reputation, qualification=FakeQualification({"a1"}))
    result = selector.select(selection_id="s2", task_id="t2", capability="crm.read", candidate_agent_ids=("a1","a2"), requested_scope={"workspace_id":"ws-1"}, budget_ids={"a1":"b1","a2":"b2"})
    assert result.selected_agent_id == "a1"

def test_qualification_is_a_gate_not_a_score():
    base, reputation = setup()
    selector = ReputationAwareWorkerSelectionRegistry("ws-1", base, reputation, qualification=FakeQualification({"a2"}))
    result = selector.select(selection_id="s3", task_id="t3", capability="crm.read", candidate_agent_ids=("a1","a2"), requested_scope={"workspace_id":"ws-1"}, budget_ids={"a1":"b1","a2":"b2"})
    assert result.selected_agent_id == "a2"
    assert tuple(c.agent_id for c in result.candidates) == ("a2",)

def test_qualification_workspace_is_fenced():
    base, reputation = setup()
    bad = FakeQualification({"a1"}); bad.workspace_id = "ws-2"
    with pytest.raises(ValueError, match=ReputationSelectionBlock.TENANT_MISMATCH):
        ReputationAwareWorkerSelectionRegistry("ws-1", base, reputation, qualification=bad)
