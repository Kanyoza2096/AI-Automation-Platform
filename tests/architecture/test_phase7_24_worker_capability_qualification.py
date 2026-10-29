from services.v71_35_worker_capability_qualification import (
    QualificationStatus, WorkerCapabilityQualification, WorkerCapabilityQualificationRegistry
)

def test_phase724_service_boundary():
    assert WorkerCapabilityQualificationRegistry.VERSION == 'phase7.24'

def test_phase724_statuses_are_bounded():
    assert {x.value for x in QualificationStatus} == {'qualified','expired','suspended'}

def test_phase724_model_is_advisory():
    fields=WorkerCapabilityQualification.__dataclass_fields__
    assert fields['advisory_only'].default is True
    assert fields['authority_granted'].default is False
    assert fields['execution_requested'].default is False
    assert fields['provider_called'].default is False

def test_phase724_no_execution_symbols():
    import inspect
    src=inspect.getsource(WorkerCapabilityQualificationRegistry)
    forbidden=('ActionKernel','invoke_action','call_provider','authorize(')
    assert not any(x in src for x in forbidden)

def test_phase724_uses_verified_reputation_boundary():
    import inspect
    src=inspect.getsource(WorkerCapabilityQualificationRegistry.certify)
    assert 'verified_sample_count' in src and 'average_capability_score' in src and 'average_performance_score' in src

def test_phase724_requires_governed_qualifier():
    import inspect
    src=inspect.getsource(WorkerCapabilityQualificationRegistry.certify)
    assert 'SELF_CERTIFICATION' in src and 'qualified_by' in src

def test_phase724_persists_fingerprint():
    import inspect
    src=inspect.getsource(WorkerCapabilityQualificationRegistry)
    assert 'qualification_fingerprint' in src and 'CREATE TABLE IF NOT EXISTS worker_capability_qualifications' in src

def test_phase724_workspace_fence():
    import inspect
    src=inspect.getsource(WorkerCapabilityQualificationRegistry.__init__)
    assert 'workspace_id' in src and 'TENANT_MISMATCH' in src
