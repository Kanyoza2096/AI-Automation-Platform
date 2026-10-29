from services.v71_31_worker_selection_policy import WorkerSelectionPolicyRegistry, WorkerSelectionPolicyProfile
from services.v71_32_worker_selection_policy_governance import WorkerSelectionPolicyGovernance
from services.v71_34_worker_selection_policy_audit import WorkerSelectionPolicyAudit

def setup():
    pr=WorkerSelectionPolicyRegistry('ws-23')
    p=pr.publish(WorkerSelectionPolicyProfile('ops','ws-23',1,allowed_capabilities=('security',)))
    g=WorkerSelectionPolicyGovernance('ws-23')
    c=g.propose(change_id='c1',policy_id='ops',version=1,requested_by='owner',policy_fingerprint=p.policy_fingerprint)
    g.transition('c1',actor='reviewer',role='policy_reviewer',target='REVIEWED')
    g.transition('c1',actor='approver',role='policy_approver',target='APPROVED')
    g.transition('c1',actor='approver',role='policy_approver',target='ACTIVE')
    return g, WorkerSelectionPolicyAudit('ws-23',g)

def test_full_history():
    g,a=setup(); h=a.history(policy_id='ops'); assert [x['to_state'] for x in h]==['PROPOSED','REVIEWED','APPROVED','ACTIVE']

def test_lifecycle_projection():
    g,a=setup(); x=a.lifecycle('c1'); assert x['terminal_state']=='ACTIVE' and x['read_only'] is True

def test_event_fingerprints():
    g,a=setup(); assert a.verify_chain(policy_id='ops')['integrity']=='PASS'

def test_workspace_fence():
    g,a=setup()
    try: WorkerSelectionPolicyAudit('other',g)
    except ValueError as e: assert str(e)=='tenant_mismatch'
    else: raise AssertionError

def test_no_authority():
    text=open('services/v71_34_worker_selection_policy_audit.py').read()
    assert 'execute_action' not in text and 'provider_call(' not in text and 'authorize(' not in text
