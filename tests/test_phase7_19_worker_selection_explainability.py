from services.v71_29_reputation_aware_worker_selection import ReputationAwareWorkerSelectionRegistry
from services.v71_30_worker_selection_explainability import WorkerSelectionExplainabilityRegistry

class _Profile:
    workspace_id = "ws-1"
    def profile(self, *, worker_id, capability):
        return type("P", (), {"sample_count": 5, "average_capability_score": 1.0,
            "average_performance_score": 1.0, "verified_sample_count": 5,
            "can_authorize": False, "can_execute": False, "can_call_provider": False})()

def _selector():
    class Candidate:
        def __init__(self, aid, score):
            self.agent_id=aid; self.capability_match=1.; self.capacity_score=score
            self.safety_score=1.; self.autonomy_score=1.; self.budget_score=1.
            self.total_score=score; self.reasons=("eligible",)
    class Base:
        workspace_id="ws-1"
        def select(self, **kwargs):
            from services.v71_9_worker_selection import WorkerSelection
            cs=(Candidate("worker-a", .9), Candidate("worker-b", .7))
            return WorkerSelection("s19","ws-1","task-19","ops",{},"worker-a",cs,True,False,False,"")
    return ReputationAwareWorkerSelectionRegistry("ws-1", Base(), _Profile())

def test_phase719_explainability_is_durable_and_authority_free():
    selector=_selector(); result=selector.select(selection_id="s19",task_id="task-19",capability="ops")
    registry=WorkerSelectionExplainabilityRegistry("ws-1",selector)
    evidence=registry.explain(evidence_id="e19",selection_id=result.selection_id)
    assert evidence.selected_agent_id == "worker-a"
    assert evidence.candidates[0].selected is True
    assert evidence.candidates[0].rank == 1
    assert evidence.advisory_only is True
    assert evidence.authority_granted is False
    assert evidence.execution_requested is False
    registry.assert_integrity(evidence)

def test_phase719_binding_constraints_are_visible():
    selector=_selector(); selector.select(selection_id="s19",task_id="task-19",capability="ops")
    registry=WorkerSelectionExplainabilityRegistry("ws-1",selector)
    evidence=registry.explain(evidence_id="e19",selection_id="s19")
    assert any(x.startswith("capacity=") for x in evidence.binding_constraints) is True

def test_phase719_tamper_and_workspace_fence():
    selector=_selector(); selector.select(selection_id="s19",task_id="task-19",capability="ops")
    registry=WorkerSelectionExplainabilityRegistry("ws-1",selector)
    evidence=registry.explain(evidence_id="e19",selection_id="s19")
    from dataclasses import replace
    try: registry.assert_integrity(replace(evidence, decision_summary="tampered"))
    except ValueError as exc: assert str(exc) == "evidence_tampered"
    else: raise AssertionError("tampered evidence accepted")

def test_phase719_duplicate_selection_evidence_rejected():
    selector=_selector(); selector.select(selection_id="s19",task_id="task-19",capability="ops")
    registry=WorkerSelectionExplainabilityRegistry("ws-1",selector)
    registry.explain(evidence_id="e19",selection_id="s19")
    try: registry.explain(evidence_id="e20",selection_id="s19")
    except ValueError as exc: assert str(exc) == "duplicate"
    else: raise AssertionError("duplicate selection evidence accepted")
