from services.autonomous_exception_recovery import ExceptionObservation, RecoveryDisposition, RecoveryAction
from services.v69_integration_recovery import (
    IntegrationRecoveryCoordinator, ReconciliationObservation, ReconciliationState,
    RecoveryBlock,
)


def obs(**kw):
    base = dict(workspace_id="w1", execution_id="e1", action_type="publish",
                observed_status="uncertain", external_state_uncertain=True)
    base.update(kw)
    return ExceptionObservation(**base)


def test_uncertain_without_reconciler_freezes_retry():
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=obs(), reconciliation=None, idempotent=True)
    assert d.disposition == RecoveryDisposition.RECONCILE_FIRST
    assert d.action == RecoveryAction.RECONCILE_EXTERNAL_STATE
    assert RecoveryBlock.RECONCILIATION_REQUIRED.value in d.reason_codes
    assert not d.allowed


def test_authoritative_success_closes_recovery_without_retry():
    r = ReconciliationObservation("w1", "e1", ReconciliationState.CONFIRMED_SUCCESS, authoritative=True, evidence="provider confirmed")
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=obs(), reconciliation=r, idempotent=True)
    assert d.allowed is True
    assert d.action == RecoveryAction.NONE


def test_inconclusive_never_becomes_retry():
    r = ReconciliationObservation("w1", "e1", ReconciliationState.INCONCLUSIVE, evidence="eventual consistency")
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=obs(), reconciliation=r, idempotent=True)
    assert d.disposition == RecoveryDisposition.RECONCILE_FIRST
    assert not d.allowed


def test_confirmed_absent_can_release_idempotent_transient_retry():
    r = ReconciliationObservation("w1", "e1", ReconciliationState.CONFIRMED_ABSENT, authoritative=True)
    o = obs(error="timeout", observed_status="uncertain")
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=o, reconciliation=r, idempotent=True, prior_failures=0)
    assert d.allowed is True
    assert d.action == RecoveryAction.RETRY_EXISTING_INTENT


def test_confirmed_absent_non_idempotent_requires_review():
    r = ReconciliationObservation("w1", "e1", ReconciliationState.CONFIRMED_ABSENT, authoritative=True)
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=obs(), reconciliation=r, idempotent=False)
    assert d.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert RecoveryBlock.RETRY_UNSAFE.value in d.reason_codes
    assert d.requires_human_approval


def test_irreversible_recovery_never_auto_retries():
    r = ReconciliationObservation("w1", "e1", ReconciliationState.CONFIRMED_ABSENT, authoritative=True)
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=obs(), reconciliation=r, idempotent=True, irreversible=True)
    assert d.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert not d.allowed


def test_retry_budget_exhaustion_quarantines():
    r = ReconciliationObservation("w1", "e1", ReconciliationState.CONFIRMED_ABSENT, authoritative=True)
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=obs(error="timeout"), reconciliation=r, idempotent=True, prior_failures=3)
    assert d.disposition == RecoveryDisposition.QUARANTINED
    assert not d.allowed


def test_non_uncertain_transient_can_auto_recover_only_if_idempotent():
    o = ExceptionObservation(workspace_id="w1", execution_id="e2", action_type="sync", observed_status="failed", error="timeout")
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=o, reconciliation=None, idempotent=True)
    assert d.disposition == RecoveryDisposition.AUTO_RECOVER
    assert d.allowed


def test_non_uncertain_non_idempotent_is_review():
    o = ExceptionObservation(workspace_id="w1", execution_id="e2", action_type="sync", observed_status="failed", error="timeout")
    d = IntegrationRecoveryCoordinator().decide_after_reconciliation(observation=o, reconciliation=None, idempotent=False)
    assert d.disposition == RecoveryDisposition.REVIEW_REQUIRED
    assert not d.allowed
