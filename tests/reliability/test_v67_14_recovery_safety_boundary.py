from services.autonomous_exception_recovery import (
    ExceptionObservation, RecoveryGovernance, RecoveryDisposition, RecoveryAction,
)
from services.v67_recovery_safety_boundary import certify_recovery_release, assert_no_parallel_execution_path


def plan_ok():
    obs = ExceptionObservation(
        workspace_id="ws1", execution_id="ex1", intent_id="act1",
        action_type="crm_add_contact", observed_status="failed", error="timeout",
    )
    return RecoveryGovernance().plan(obs, idempotent=True, prior_failures=0)


def test_valid_recovery_reenters_same_governed_path():
    plan = plan_ok()
    assert plan.disposition is RecoveryDisposition.AUTO_RECOVER
    d = certify_recovery_release(
        plan, intent_id="act1", original_intent_id="act1",
        current_action_type="crm_add_contact", original_action_type="crm_add_contact",
        current_workspace_id="ws1",
    )
    assert d.allowed is True
    assert d.authority == "none"
    assert d.execution_path == "action_outbox->action_kernel"


def test_lineage_drift_fails_closed():
    plan = plan_ok()
    d = certify_recovery_release(
        plan, intent_id="act2", original_intent_id="act1",
        current_action_type="crm_add_contact", original_action_type="crm_add_contact",
        current_workspace_id="ws1",
    )
    assert not d.allowed


def test_action_type_drift_fails_closed():
    plan = plan_ok()
    d = certify_recovery_release(
        plan, intent_id="act1", original_intent_id="act1",
        current_action_type="crm_add_meeting", original_action_type="crm_add_contact",
        current_workspace_id="ws1",
    )
    assert not d.allowed


def test_workspace_drift_fails_closed():
    plan = plan_ok()
    d = certify_recovery_release(
        plan, intent_id="act1", original_intent_id="act1",
        current_action_type="crm_add_contact", original_action_type="crm_add_contact",
        current_workspace_id="ws2",
    )
    assert not d.allowed


def test_recovery_never_consumes_approval_as_autonomous_authority():
    plan = plan_ok()
    d = certify_recovery_release(
        plan, intent_id="act1", original_intent_id="act1",
        current_action_type="crm_add_contact", original_action_type="crm_add_contact",
        current_workspace_id="ws1", approval_present=True,
    )
    assert not d.allowed


def test_uncertain_reconciliation_plan_cannot_be_released_as_retry():
    obs = ExceptionObservation(
        workspace_id="ws1", execution_id="ex2", intent_id="act2",
        action_type="crm_add_contact", observed_status="uncertain",
        external_state_uncertain=True,
    )
    plan = RecoveryGovernance().plan(obs, idempotent=True, has_reconciler=True)
    assert plan.disposition is RecoveryDisposition.RECONCILE_FIRST
    d = certify_recovery_release(
        plan, intent_id="act2", original_intent_id="act2",
        current_action_type="crm_add_contact", original_action_type="crm_add_contact",
        current_workspace_id="ws1",
    )
    assert not d.allowed


def test_irreversible_recovery_cannot_be_auto_released():
    obs = ExceptionObservation(
        workspace_id="ws1", execution_id="ex3", intent_id="act3",
        action_type="dangerous_action", observed_status="failed", error="timeout",
    )
    plan = RecoveryGovernance().plan(obs, idempotent=True, irreversible=True)
    assert plan.disposition is RecoveryDisposition.REVIEW_REQUIRED
    assert plan.action is RecoveryAction.RETRY_EXISTING_INTENT
    d = certify_recovery_release(
        plan, intent_id="act3", original_intent_id="act3",
        current_action_type="dangerous_action", original_action_type="dangerous_action",
        current_workspace_id="ws1",
    )
    assert not d.allowed


def test_repeated_failures_quarantine_before_unbounded_retry():
    obs = ExceptionObservation(
        workspace_id="ws1", execution_id="ex4", intent_id="act4",
        action_type="crm_add_contact", observed_status="failed", error="timeout", attempt=3,
    )
    plan = RecoveryGovernance().plan(obs, idempotent=True, prior_failures=3)
    assert plan.disposition is RecoveryDisposition.QUARANTINED
    assert plan.action is RecoveryAction.NONE


def test_parallel_provider_metadata_is_rejected():
    try:
        assert_no_parallel_execution_path({"direct_provider_call": True})
    except ValueError:
        return
    assert False, "parallel execution metadata must be rejected"
