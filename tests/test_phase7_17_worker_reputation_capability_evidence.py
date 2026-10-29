from services.v71_28_worker_reputation_capability_evidence import (
    WorkerReputationCapabilityRegistry, ReputationBlock
)

def test_phase717_version_and_safe_boundary():
    assert WorkerReputationCapabilityRegistry.VERSION == "phase7.17"
    assert ReputationBlock.SELF_MUTATION.value == "self_mutation"

def test_phase717_score_bounds():
    assert WorkerReputationCapabilityRegistry._score(0) == 0.0
    assert WorkerReputationCapabilityRegistry._score(1) == 1.0
    for value in (-0.01, 1.01):
        try: WorkerReputationCapabilityRegistry._score(value)
        except ValueError as exc: assert str(exc) == "invalid_score"
        else: raise AssertionError("invalid score accepted")

from services.v71_27_worker_learning_feedback import WorkerLearningFeedback
from services.v71_15_worker_outcome_feedback_closure import ClosureState, VerifiedDisposition

class _LearningFixture:
    workspace_id = "ws-1"
    def __init__(self):
        self.feedback = WorkerLearningFeedback(
            "fb-1", "ws-1", "cl-1", "as-1", "task-1", "worker-1",
            VerifiedDisposition.VERIFIED_SUCCESS, ClosureState.CLOSED,
            0.9, 0.95, 0.9, "verified_outcome_feedback", "closure-fp",
            True, False, False, False, False, ""
        )
        self.feedback = WorkerLearningFeedback(
            **{**self.feedback.__dict__, "feedback_fingerprint": self.feedback.fingerprint()}
        )
    def get(self, feedback_id):
        return self.feedback if feedback_id == self.feedback.feedback_id else None
    def assert_integrity(self, record):
        assert record.feedback_fingerprint == record.fingerprint()

def test_phase717_derives_capability_evidence_from_verified_feedback():
    registry = WorkerReputationCapabilityRegistry("ws-1", _LearningFixture())
    evidence = registry.record(
        evidence_id="ev-1", feedback_id="fb-1", capability="security.contain_asset",
        capability_score=0.92,
    )
    assert evidence.verified is True
    assert evidence.worker_mutable is False
    assert evidence.authorization_granted is False
    assert evidence.execution_requested is False
    assert evidence.provider_called is False
    profile = registry.profile(worker_id="worker-1", capability="security.contain_asset")
    assert profile.sample_count == 1
    assert profile.average_capability_score == 0.92
    assert profile.average_performance_score == 0.9
    assert profile.can_execute is False
    registry.close()

def test_phase717_duplicate_feedback_capability_is_rejected():
    registry = WorkerReputationCapabilityRegistry("ws-1", _LearningFixture())
    registry.record(evidence_id="ev-1", feedback_id="fb-1", capability="security.contain_asset", capability_score=0.8)
    try:
        registry.record(evidence_id="ev-2", feedback_id="fb-1", capability="security.contain_asset", capability_score=0.9)
    except ValueError as exc:
        assert str(exc) == "duplicate"
    else: raise AssertionError("duplicate evidence accepted")
    registry.close()

def test_phase717_evidence_cannot_be_worker_mutated_or_granted_authority():
    registry = WorkerReputationCapabilityRegistry("ws-1", _LearningFixture())
    evidence = registry.record(evidence_id="ev-1", feedback_id="fb-1", capability="ops", capability_score=0.7)
    tampered = type(evidence)(**{**evidence.__dict__, "worker_mutable": True})
    try: registry.assert_integrity(tampered)
    except ValueError as exc: assert str(exc) == "evidence_tampered"
    else: raise AssertionError("worker mutation accepted")
    registry.close()

def test_phase717_workspace_fencing_is_explicit():
    try: WorkerReputationCapabilityRegistry("ws-2", _LearningFixture())
    except ValueError as exc: assert str(exc) == "tenant_mismatch"
    else: raise AssertionError("cross-workspace registry accepted")
