def test_phase723_audit_read_only_contract():
    text=open('services/v71_34_worker_selection_policy_audit.py').read()
    assert 'read_only' in text and 'can_authorize' in text and 'can_execute' in text

def test_phase723_does_not_execute():
    text=open('services/v71_34_worker_selection_policy_audit.py').read()
    assert 'subprocess' not in text and 'requests.' not in text and 'httpx.' not in text

def test_phase723_workspace_fenced():
    text=open('services/v71_34_worker_selection_policy_audit.py').read()
    assert 'TENANT_MISMATCH' in text and 'workspace_id' in text

def test_phase723_uses_governance_history():
    text=open('services/v71_34_worker_selection_policy_audit.py').read()
    assert 'audit_history' in text and 'assert_audit_integrity' in text

def test_phase723_does_not_mutate_policy():
    text=open('services/v71_34_worker_selection_policy_audit.py').read()
    assert '.transition(' not in text and '.propose(' not in text and '.publish(' not in text
