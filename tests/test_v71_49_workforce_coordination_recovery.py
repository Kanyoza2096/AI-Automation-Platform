import pytest
from services.v71_46_workforce_adaptive_planning import WorkforceAdaptivePlanner, WorkforceCoordinationPolicy
from services.v71_47_workforce_coordination_policy_governance import WorkforceCoordinationPolicyGovernance
from services.v71_48_workforce_coordination_policy_enforcement import WorkforceCoordinationPolicyEnforcement
from services.v71_49_workforce_coordination_recovery import WorkforceCoordinationRecoveryReconciler, WorkforceRecoveryBlock
from tests.test_v71_48_workforce_coordination_policy_enforcement import _stack, _active, _plan

def test_clean_recovery_is_recovered(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy=WorkforceCoordinationPolicy(require_verified_feedback=False); _active(governance,policy)
    enforcement.assess_governed(assessment_id="a40",binding_id="b40",policy_id="adaptive",plan=_plan(planner,"p40"),policy=policy,feedback_ids=())
    r=WorkforceCoordinationRecoveryReconciler("ws",governance,enforcement,str(tmp_path/"recovery.db"))
    report=r.reconcile(report_id="r40")
    assert report.status=="RECOVERED" and report.healthy==1 and report.degraded==0
    assert r.context("r40")["authority"]=="none"

def test_stale_binding_requires_reconciliation(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy=WorkforceCoordinationPolicy(require_verified_feedback=False); _active(governance,policy)
    enforcement.assess_governed(assessment_id="a40",binding_id="b40",policy_id="adaptive",plan=_plan(planner,"p40"),policy=policy,feedback_ids=())
    enforcement._db.execute("UPDATE workforce_coordination_policy_consumption_bindings SET policy_version='99' WHERE binding_id='b40'"); enforcement._db.commit()
    r=WorkforceCoordinationRecoveryReconciler("ws",governance,enforcement,str(tmp_path/"recovery.db")); report=r.reconcile(report_id="r40")
    assert report.status=="RECONCILIATION_REQUIRED" and any(f.reason in ("binding_tampered","stale_policy") for f in report.findings)

def test_missing_audit_event_is_detected_without_repair(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy=WorkforceCoordinationPolicy(require_verified_feedback=False); _active(governance,policy)
    r=WorkforceCoordinationRecoveryReconciler("ws",governance,enforcement,str(tmp_path/"recovery.db"))
    governance._db.execute("DELETE FROM workforce_coordination_policy_audit"); governance._db.commit()
    report=r.reconcile(report_id="r40")
    assert report.status=="RECONCILIATION_REQUIRED" and any(f.reason==WorkforceRecoveryBlock.AUDIT_DIVERGENCE for f in report.findings)
    assert governance.active("adaptive") is not None

def test_report_tamper_duplicate_and_workspace(tmp_path):
    planner, adaptive, governance, enforcement = _stack(tmp_path)
    policy=WorkforceCoordinationPolicy(require_verified_feedback=False); _active(governance,policy)
    r=WorkforceCoordinationRecoveryReconciler("ws",governance,enforcement,str(tmp_path/"recovery.db")); report=r.reconcile(report_id="r40")
    r.assert_report_integrity(report)
    bad=type(report)(**{**report.__dict__,"status":"RECOVERED" if report.status!="RECOVERED" else "RECONCILIATION_REQUIRED"})
    with pytest.raises(ValueError,match="tampered"): r.assert_report_integrity(bad)
    with pytest.raises(ValueError,match="duplicate"): r.reconcile(report_id="r40")
    with pytest.raises(ValueError,match="tenant_mismatch"): WorkforceCoordinationRecoveryReconciler("other",governance,enforcement)
