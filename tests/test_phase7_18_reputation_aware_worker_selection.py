from services.v71_28_worker_reputation_capability_evidence import WorkerReputationCapabilityRegistry
from services.v71_29_reputation_aware_worker_selection import (
    ReputationAwareWorkerSelectionRegistry, ReputationSelectionPolicy
)

class _Profile:
    workspace_id = "ws-1"
    def __init__(self, profiles): self.profiles = profiles
    def profile(self, *, worker_id, capability):
        return self.profiles.get((worker_id, capability), type("P", (), {
            "sample_count": 0, "average_capability_score": 0.0,
            "average_performance_score": 0.0, "verified_sample_count": 0,
            "can_authorize": False, "can_execute": False, "can_call_provider": False,
        })())


def test_phase718_policy_bounds():
    ReputationSelectionPolicy().validate()
    try: ReputationSelectionPolicy(max_influence=0.11).validate()
    except ValueError as exc: assert str(exc) == "invalid_policy"
    else: raise AssertionError("unsafe reputation influence accepted")


def test_phase718_registry_is_workspace_fenced():
    class Base: workspace_id = "ws-1"
    try: ReputationAwareWorkerSelectionRegistry("ws-2", Base(), _Profile({}))
    except ValueError as exc: assert str(exc) == "tenant_mismatch"
    else: raise AssertionError("cross-workspace selector accepted")


def test_phase718_reputation_is_advisory_and_bounded():
    class Candidate:
        agent_id = "worker-1"; capability_match = 1.0; capacity_score = .8
        safety_score = 1.0; autonomy_score = 1.0; budget_score = 1.0
        total_score = .8; reasons = ()
    class Base:
        workspace_id = "ws-1"
        def select(self, **kwargs):
            from services.v71_9_worker_selection import WorkerSelection
            c = Candidate()
            return WorkerSelection("s1", "ws-1", "t1", "ops", {}, "worker-1", (c,), True, False, False, "")
    profile = type("P", (), {"sample_count": 5, "average_capability_score": 1.0,
                              "average_performance_score": 1.0, "verified_sample_count": 5,
                              "can_authorize": False, "can_execute": False,
                              "can_call_provider": False})()
    registry = ReputationAwareWorkerSelectionRegistry("ws-1", Base(), _Profile({("worker-1", "ops"): profile}))
    result = registry.select(selection_id="s1", task_id="t1", capability="ops")
    assert result.selected_agent_id == "worker-1"
    assert result.advisory_only is True
    assert result.authority_granted is False
    assert result.execution_requested is False
    assert result.candidates[0].total_score == .8
    registry.assert_integrity(result)


def test_phase718_reputation_cannot_create_eligibility():
    class Base:
        workspace_id = "ws-1"
        def select(self, **kwargs):
            from services.v71_9_worker_selection import WorkerSelection
            return WorkerSelection("s1", "ws-1", "t1", "ops", {}, None, (), True, False, False, "")
    profile = type("P", (), {"sample_count": 100, "average_capability_score": 1.0,
                              "average_performance_score": 1.0, "verified_sample_count": 100,
                              "can_authorize": False, "can_execute": False,
                              "can_call_provider": False})()
    registry = ReputationAwareWorkerSelectionRegistry("ws-1", Base(), _Profile({("worker-1", "ops"): profile}))
    result = registry.select(selection_id="s1", task_id="t1", capability="ops")
    assert result.selected_agent_id is None
    assert result.candidates == ()
