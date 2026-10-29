import pytest

from services.phase9_failure_policy import OperationRisk, PolicyDecision
from services.phase9_reliability import DependencyState
from services.phase9_reconciliation import ReconciliationStatus
from services.phase9_resilience import (
    FailureInjection,
    FailureMode,
    ResilienceOutcome,
    ResilienceScenario,
    run_scenario,
)


def scenario(**kw):
    data = dict(
        name="provider timeout",
        dependency_id="payments",
        initial_state=DependencyState.UNAVAILABLE,
        failure_mode=FailureMode.TIMEOUT,
        risk=OperationRisk.HIGH,
        expected_policy_decision=PolicyDecision.BLOCK,
        expected_outcome=ResilienceOutcome.BLOCKED,
    )
    data.update(kw)
    return ResilienceScenario(**data)


def test_timeout_blocks_high_risk_operation():
    result = run_scenario(scenario())
    assert result.passed
    assert result.policy_decision == PolicyDecision.BLOCK
    assert result.outcome == ResilienceOutcome.BLOCKED


def test_unknown_is_held():
    s = scenario(
        initial_state=DependencyState.UNKNOWN,
        failure_mode=FailureMode.CONNECTION_FAILURE,
        expected_policy_decision=PolicyDecision.HOLD,
        expected_outcome=ResilienceOutcome.HOLD,
    )
    result = run_scenario(s)
    assert result.passed


def test_recovering_is_not_normal_execution():
    s = scenario(
        initial_state=DependencyState.RECOVERING,
        failure_mode=FailureMode.RECOVERY,
        expected_policy_decision=PolicyDecision.RECOVERY_REQUIRED,
        expected_outcome=ResilienceOutcome.HOLD,
    )
    result = run_scenario(s)
    assert result.passed


def test_security_hold_blocks():
    s = scenario(
        initial_state=DependencyState.SECURITY_HOLD,
        failure_mode=FailureMode.ERROR_RESPONSE,
        risk=OperationRisk.LOW,
        expected_policy_decision=PolicyDecision.SECURITY_HOLD,
        expected_outcome=ResilienceOutcome.BLOCKED,
    )
    assert run_scenario(s).passed


def test_quarantine_blocks():
    s = scenario(
        initial_state=DependencyState.QUARANTINED,
        failure_mode=FailureMode.PARTIAL_OUTAGE,
        risk=OperationRisk.LOW,
        expected_policy_decision=PolicyDecision.QUARANTINE,
        expected_outcome=ResilienceOutcome.BLOCKED,
    )
    assert run_scenario(s).passed


def test_stale_data_requires_reconciliation():
    s = scenario(
        initial_state=DependencyState.HEALTHY,
        failure_mode=FailureMode.STALE_DATA,
        risk=OperationRisk.HIGH,
        expected_policy_decision=PolicyDecision.ALLOW,
        expected_outcome=ResilienceOutcome.RECONCILIATION_REQUIRED,
    )
    result = run_scenario(s, observed_state={"status": "healthy", "version": 0})
    assert result.passed
    assert result.reconciliation_status == ReconciliationStatus.DRIFT_DETECTED


def test_inconsistent_data_requires_reconciliation():
    s = scenario(
        initial_state=DependencyState.HEALTHY,
        failure_mode=FailureMode.INCONSISTENT_DATA,
        risk=OperationRisk.MEDIUM,
        expected_policy_decision=PolicyDecision.ALLOW,
        expected_outcome=ResilienceOutcome.RECONCILIATION_REQUIRED,
    )
    result = run_scenario(
        s,
        observed_state={"status": "disabled", "version": 9},
    )
    assert result.passed


def test_read_only_degraded_operation_can_be_safe():
    s = scenario(
        initial_state=DependencyState.DEGRADED,
        failure_mode=FailureMode.PARTIAL_OUTAGE,
        risk=OperationRisk.LOW,
        read_only=True,
        expected_policy_decision=PolicyDecision.DEGRADE,
        expected_outcome=ResilienceOutcome.SAFE,
    )
    assert run_scenario(s).passed


def test_failure_injection_is_not_authority():
    with pytest.raises(ValueError):
        FailureInjection(
            workspace_id="ws_1",
            dependency_id="payments",
            mode=FailureMode.TIMEOUT,
            injected_at="2026-10-05T08:00:00Z",
            authority="executor",
        )


def test_scenario_is_not_authority():
    with pytest.raises(ValueError):
        scenario(authority="executor")


def test_result_is_not_authority():
    result = run_scenario(scenario())
    with pytest.raises(ValueError):
        result.__class__(
            scenario_id=result.scenario_id,
            outcome=result.outcome,
            policy_decision=result.policy_decision,
            reconciliation_status=result.reconciliation_status,
            assertions=result.assertions,
            passed=result.passed,
            authority="executor",
        )


def test_workspace_context_is_explicit():
    result = run_scenario(scenario(), workspace_id="ws_enterprise")
    assert result.passed


def test_repeated_same_scenario_is_deterministic():
    s = scenario()
    a = run_scenario(s)
    b = run_scenario(s)
    assert a.outcome == b.outcome
    assert a.policy_decision == b.policy_decision
    assert a.reconciliation_status == b.reconciliation_status


def test_observation_mismatch_overrides_nominal_health_for_safety():
    s = scenario(
        initial_state=DependencyState.HEALTHY,
        failure_mode=FailureMode.INCONSISTENT_DATA,
        risk=OperationRisk.LOW,
        expected_policy_decision=PolicyDecision.ALLOW,
        expected_outcome=ResilienceOutcome.RECONCILIATION_REQUIRED,
    )
    result = run_scenario(
        s,
        observed_state={"status": "unknown", "version": 999},
    )
    assert result.outcome == ResilienceOutcome.RECONCILIATION_REQUIRED
