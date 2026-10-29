import pytest
from services.v71_47_workforce_coordination_policy_governance import WorkforceCoordinationPolicyGovernance, CoordinationPolicyGovernanceBlock

def test_lifecycle_and_activation():
    g=WorkforceCoordinationPolicyGovernance("w")
    c=g.propose(change_id="c1",policy_id="adaptive",version=1,requested_by="owner",policy_fingerprint="pf",role="policy_owner")
    c=g.transition("c1",actor="reviewer",role="policy_reviewer",target="REVIEWED")
    with pytest.raises(ValueError,match="separation_of_duties"): g.transition("c1",actor="reviewer",role="policy_approver",target="APPROVED")
    c=g.transition("c1",actor="approver",role="policy_approver",target="APPROVED")
    c=g.transition("c1",actor="activator",role="policy_approver",target="ACTIVE")
    assert g.active("adaptive").version==1
    assert len(g.audit_history(policy_id="adaptive"))==4

def test_single_active_and_immutable_integrity():
    g=WorkforceCoordinationPolicyGovernance("w")
    for i in (1,2):
        g.propose(change_id=f"c{i}",policy_id="adaptive",version=i,requested_by=f"o{i}",policy_fingerprint=f"p{i}")
        g.transition(f"c{i}",actor=f"r{i}",role="policy_reviewer",target="REVIEWED")
        g.transition(f"c{i}",actor=f"a{i}",role="policy_approver",target="APPROVED")
    g.transition("c1",actor="x",role="policy_approver",target="ACTIVE")
    with pytest.raises(ValueError,match="active_conflict"): g.transition("c2",actor="y",role="policy_approver",target="ACTIVE")
    c=g.active("adaptive")
    bad=type(c)(**{**c.__dict__,"policy_fingerprint":"tampered"})
    with pytest.raises(ValueError,match="tampered"): g.assert_integrity(bad)

def test_workspace_and_audit_fencing():
    g=WorkforceCoordinationPolicyGovernance("w")
    c=g.propose(change_id="c",policy_id="p",version=1,requested_by="o",policy_fingerprint="f")
    ev=g.audit_history()[0]; g.assert_audit_integrity(ev)
    bad=dict(ev); bad["event_fingerprint"]="x"
    with pytest.raises(ValueError,match="tampered"): g.assert_audit_integrity(bad)
    with pytest.raises(ValueError,match="tenant_mismatch"): WorkforceCoordinationPolicyGovernance("")

def test_active_binding_requires_exact_version_and_fingerprint():
    g=WorkforceCoordinationPolicyGovernance("w")
    c=g.propose(change_id="c",policy_id="adaptive",version=3,requested_by="owner",policy_fingerprint="fp")
    g.transition("c",actor="reviewer",role="policy_reviewer",target="REVIEWED")
    g.transition("c",actor="approver",role="policy_approver",target="APPROVED")
    g.transition("c",actor="activator",role="policy_approver",target="ACTIVE")
    assert g.assert_active_binding(policy_id="adaptive",version=3,policy_fingerprint="fp").state == "ACTIVE"
    with pytest.raises(ValueError,match="tampered"): g.assert_active_binding(policy_id="adaptive",version=2,policy_fingerprint="fp")
    with pytest.raises(ValueError,match="tampered"): g.assert_active_binding(policy_id="adaptive",version=3,policy_fingerprint="bad")
