import pytest

from services.phase9_failure_policy import (
    OperationRisk,
    PolicyDecision,
    RecoveryPolicy,
    evaluate,
    recovery_transition_allowed,
)
from services.phase9_reliability import DependencyState


def policy(**kw):
    data = dict(workspace_id="ws_1", dependency_id="stripe")
    data.update(kw)
    return RecoveryPolicy(**data)


@pytest.mark.parametrize(
    "state,expected",
    [
        (DependencyState.HEALTHY, PolicyDecision.ALLOW),
        (DependencyState.UNAVAILABLE, PolicyDecision.BLOCK),
        (DependencyState.UNKNOWN, PolicyDecision.HOLD),
        (DependencyState.RECOVERING, PolicyDecision.RECOVERY_REQUIRED),
        (DependencyState.RECONCILIATION_REQUIRED, PolicyDecision.RECONCILIATION_REQUIRED),
        (DependencyState.SECURITY_HOLD, PolicyDecision.SECURITY_HOLD),
        (DependencyState.QUARANTINED, PolicyDecision.QUARANTINE),
    ],
)
def test_conservative_decisions_for_unsafe_states(state, expected):
    result = evaluate(
        policy(),
        dependency_state=state,
        risk=OperationRisk.HIGH,
    )
    assert result.decision == expected


def test_degraded_state_holds_normal_mutation():
    result = evaluate(
        policy(),
        dependency_state=DependencyState.DEGRADED,
        risk=OperationRisk.MEDIUM,
    )
    assert result.decision == PolicyDecision.HOLD
    assert not result.permits_execution


def test_read_only_can_degrade_safely_when_policy_allows_it():
    result = evaluate(
        policy(allow_read_only_when_degraded=True),
        dependency_state=DependencyState.DEGRADED,
        risk=OperationRisk.LOW,
        read_only=True,
    )
    assert result.decision == PolicyDecision.DEGRADE


def test_low_risk_degraded_execution_is_policy_explicit():
    denied = evaluate(
        policy(allow_low_risk_when_degraded=False),
        dependency_state=DependencyState.DEGRADED,
        risk=OperationRisk.LOW,
    )
    allowed = evaluate(
        policy(allow_low_risk_when_degraded=True),
        dependency_state=DependencyState.DEGRADED,
        risk=OperationRisk.LOW,
    )
    assert denied.decision == PolicyDecision.HOLD
    assert allowed.decision == PolicyDecision.DEGRADE


def test_policy_is_authority_free():
    with pytest.raises(ValueError):
        policy(authority="executor")
    result = evaluate(
        RecoveryPolicy(
            workspace_id="ws_1",
            dependency_id="stripe",
        ),
        dependency_state=DependencyState.HEALTHY,
        risk=OperationRisk.LOW,
    )
    with pytest.raises(ValueError):
        result.__class__(
            workspace_id="ws_1",
            dependency_id="stripe",
            dependency_state=DependencyState.HEALTHY,
            risk=OperationRisk.LOW,
            read_only=False,
            decision=PolicyDecision.ALLOW,
            reason="x",
            policy_id="p",
            authority="executor",
        )


def test_policy_workspace_is_preserved():
    result = evaluate(
        policy(),
        dependency_state=DependencyState.HEALTHY,
        risk=OperationRisk.LOW,
    )
    assert result.workspace_id == "ws_1"
    assert result.dependency_id == "stripe"


def test_unknown_never_becomes_allow():
    result = evaluate(
        policy(),
        dependency_state=DependencyState.UNKNOWN,
        risk=OperationRisk.LOW,
        read_only=True,
    )
    assert result.decision != PolicyDecision.ALLOW


def test_recovery_transitions_are_bounded():
    assert recovery_transition_allowed(
        DependencyState.UNAVAILABLE, DependencyState.RECOVERING
    )
    assert recovery_transition_allowed(
        DependencyState.RECOVERING, DependencyState.HEALTHY
    )
    assert not recovery_transition_allowed(
        DependencyState.QUARANTINED, DependencyState.HEALTHY
    )


def test_reconciliation_requires_explicit_state():
    result = evaluate(
        policy(),
        dependency_state=DependencyState.RECONCILIATION_REQUIRED,
        risk=OperationRisk.CRITICAL,
    )
    assert result.decision == PolicyDecision.RECONCILIATION_REQUIRED
    assert "reconciled" in result.reason


def test_security_hold_cannot_be_downgraded_to_normal_execution():
    result = evaluate(
        policy(),
        dependency_state=DependencyState.SECURITY_HOLD,
        risk=OperationRisk.LOW,
        read_only=True,
    )
    assert result.decision == PolicyDecision.SECURITY_HOLD


def test_quarantine_is_stronger_than_degradation():
    result = evaluate(
        policy(allow_low_risk_when_degraded=True),
        dependency_state=DependencyState.QUARANTINED,
        risk=OperationRisk.LOW,
    )
    assert result.decision == PolicyDecision.QUARANTINE


def test_evaluation_serialization_is_evidence_friendly():
    result = evaluate(
        policy(),
        dependency_state=DependencyState.DEGRADED,
        risk=OperationRisk.HIGH,
    )
    data = result.to_dict()
    assert data["dependency_state"] == "DEGRADED"
    assert data["risk"] == "HIGH"
    assert data["decision"] == "HOLD"
    assert data["authority"] == "none"
