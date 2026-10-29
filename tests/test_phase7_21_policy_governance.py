from services.v71_32_worker_selection_policy_governance import WorkerSelectionPolicyGovernance

def make(g):
    return g.propose(change_id='c1', policy_id='ops', version=1, requested_by='alice', policy_fingerprint='fp1')

def test_lifecycle():
    g=WorkerSelectionPolicyGovernance('ws-21'); make(g)
    g.transition('c1',actor='bob',role='policy_reviewer',target='REVIEWED')
    g.transition('c1',actor='carol',role='policy_approver',target='APPROVED')
    c=g.transition('c1',actor='carol',role='policy_approver',target='ACTIVE')
    assert c.state=='ACTIVE' and g.active('ops').change_id=='c1'

def test_separation_of_duties():
    g=WorkerSelectionPolicyGovernance('ws-21'); make(g)
    try: g.transition('c1',actor='alice',role='policy_owner',target='REVIEWED')
    except ValueError as e: assert str(e)=='role_required'
    else: raise AssertionError('owner approved review')

def test_no_active_conflict():
    g=WorkerSelectionPolicyGovernance('ws-21'); make(g)
    g.transition('c1',actor='r',role='policy_reviewer',target='REVIEWED'); g.transition('c1',actor='a',role='policy_approver',target='APPROVED'); g.transition('c1',actor='a',role='policy_approver',target='ACTIVE')
    g.propose(change_id='c2', policy_id='ops', version=2, requested_by='alice', policy_fingerprint='fp2')
    g.transition('c2',actor='r',role='policy_reviewer',target='REVIEWED'); g.transition('c2',actor='a',role='policy_approver',target='APPROVED')
    try: g.transition('c2',actor='a',role='policy_approver',target='ACTIVE')
    except ValueError as e: assert str(e)=='active_conflict'
    else: raise AssertionError('two active policies accepted')

def test_tamper():
    from dataclasses import replace
    g=WorkerSelectionPolicyGovernance('ws-21'); c=make(g)
    try: g.assert_integrity(replace(c, policy_fingerprint='evil'))
    except ValueError as e: assert str(e)=='tampered'
    else: raise AssertionError('tamper accepted')

def test_authority_boundary():
    text=open('services/v71_32_worker_selection_policy_governance.py').read()
    assert 'execute_action' not in text and 'invoke_action_kernel' not in text and 'provider_call(' not in text
