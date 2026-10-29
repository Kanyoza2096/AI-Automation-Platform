import pytest
from types import SimpleNamespace
from services.v71_35_worker_capability_qualification import *

class FakeReputation:
    workspace_id = "ws"
    def __init__(self, samples=3, score=.9):
        self.samples=samples; self.score=score
    def profile(self, *, worker_id, capability):
        ids=tuple(f"e-{i}" for i in range(self.samples))
        return SimpleNamespace(workspace_id="ws", worker_id=worker_id, capability=capability, sample_count=self.samples, average_capability_score=self.score, average_performance_score=self.score, verified_sample_count=self.samples, evidence_ids=ids, can_authorize=False, can_execute=False, can_call_provider=False)

def test_qualifies_from_verified_evidence():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation())
    rec=q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='governance',issued_at='2026-10-05T08:00:00Z')
    assert rec.status==QualificationStatus.QUALIFIED and q.is_currently_qualified('worker-1','security')

def test_insufficient_evidence_fails_closed():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation(samples=2))
    with pytest.raises(ValueError,match=QualificationBlock.INSUFFICIENT_EVIDENCE.value):
        q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='governance',issued_at='now',minimum_samples=3)

def test_qualification_is_not_authority():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation())
    rec=q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='governance',issued_at='now')
    assert rec.authority_granted is False and rec.execution_requested is False and rec.provider_called is False

def test_tamper_detected():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation())
    rec=q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='governance',issued_at='now')
    bad=WorkerCapabilityQualification(**{**rec.__dict__,'qualification_score':0.1})
    with pytest.raises(ValueError,match=QualificationBlock.QUALIFICATION_TAMPERED.value): q.assert_integrity(bad)

def test_cross_workspace_fenced():
    with pytest.raises(ValueError,match=QualificationBlock.TENANT_MISMATCH.value): WorkerCapabilityQualificationRegistry('other',FakeReputation())

def test_self_certification_rejected():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation())
    with pytest.raises(ValueError,match=QualificationBlock.SELF_CERTIFICATION.value):
        q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='',issued_at='now')

def test_low_score_rejected():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation(score=.5))
    with pytest.raises(ValueError,match=QualificationBlock.SCORE_TOO_LOW.value):
        q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='governance',issued_at='now')

def test_context_is_advisory():
    q=WorkerCapabilityQualificationRegistry('ws',FakeReputation())
    q.certify(qualification_id='q1',worker_id='worker-1',capability='security',qualified_by='governance',issued_at='now')
    c=q.context('worker-1','security')
    assert c['advisory_only'] is True and c['authority_granted'] is False and c['execution_requested'] is False
