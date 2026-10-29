from dataclasses import replace
from services.v71_31_worker_selection_policy import WorkerSelectionPolicyRegistry, WorkerSelectionPolicyProfile
from services.v71_32_worker_selection_policy_governance import WorkerSelectionPolicyGovernance
from services.v71_33_worker_selection_policy_activation import WorkerSelectionPolicyActivation


def setup():
    pr=WorkerSelectionPolicyRegistry('ws-22')
    p=pr.publish(WorkerSelectionPolicyProfile('ops','ws-22',1,allowed_capabilities=('security',)))
    g=WorkerSelectionPolicyGovernance('ws-22')
    c=g.propose(change_id='c1',policy_id='ops',version=1,requested_by='owner',policy_fingerprint=p.policy_fingerprint)
    g.transition('c1',actor='reviewer',role='policy_reviewer',target='REVIEWED')
    g.transition('c1',actor='approver',role='policy_approver',target='APPROVED')
    g.transition('c1',actor='approver',role='policy_approver',target='ACTIVE')
    a=WorkerSelectionPolicyActivation('ws-22',pr,g)
    return pr,g,a,p

def test_requires_active_governance():
    pr=WorkerSelectionPolicyRegistry('ws-22'); p=pr.publish(WorkerSelectionPolicyProfile('ops','ws-22',1))
    g=WorkerSelectionPolicyGovernance('ws-22'); a=WorkerSelectionPolicyActivation('ws-22',pr,g)
    try: a.bind_active(binding_id='b',policy_id='ops',version=1,change_id='missing',activated_by='x')
    except ValueError as e: assert str(e)=='governance_missing'
    else: raise AssertionError

def test_bind_active():
    pr,g,a,p=setup(); b=a.bind_active(binding_id='b1',policy_id='ops',version=1,change_id='c1',activated_by='approver')
    assert b.state=='BOUND' and a.active('ops').binding_id=='b1'

def test_fingerprint_binding():
    pr,g,a,p=setup(); b=a.bind_active(binding_id='b1',policy_id='ops',version=1,change_id='c1',activated_by='approver')
    assert a.enforce(policy_id='ops',capability='security')['binding_id']=='b1'
    bad=replace(b,policy_fingerprint='evil')
    try: a.assert_integrity(bad)
    except ValueError as e: assert str(e) in {'binding_tampered','fingerprint_mismatch'}
    else: raise AssertionError

def test_capability_allowlist():
    pr,g,a,p=setup(); a.bind_active(binding_id='b1',policy_id='ops',version=1,change_id='c1',activated_by='approver')
    try: a.enforce(policy_id='ops',capability='finance')
    except ValueError as e: assert str(e)=='capability_blocked'
    else: raise AssertionError

def test_idempotent_same_binding():
    pr,g,a,p=setup(); x=a.bind_active(binding_id='b1',policy_id='ops',version=1,change_id='c1',activated_by='approver'); y=a.bind_active(binding_id='b2',policy_id='ops',version=1,change_id='c1',activated_by='approver'); assert x==y

def test_no_authority():
    text=open('services/v71_33_worker_selection_policy_activation.py').read()
    assert 'execute_action' not in text and 'invoke_action_kernel' not in text and 'provider_call(' not in text and 'authorize(' not in text
