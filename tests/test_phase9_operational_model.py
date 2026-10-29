from services.phase9_operational_model import (
    OperationalEvent,
    OperationalEventType,
    OperationalState,
    can_execute_safely,
    validate_transition,
)
import pytest


def test_operational_event_is_fact_and_authority_free():
    event = OperationalEvent(
        event_type=OperationalEventType.DEPENDENCY_STATE_CHANGED,
        workspace_id="ws_1",
        occurred_at="2026-10-05T08:00:00+00:00",
        source="connector-health",
        previous_state=OperationalState.HEALTHY,
        state=OperationalState.DEGRADED,
        dependency_id="dep_1",
        correlation_id="corr_1",
    )
    assert event.authority == "none"
    assert event.to_dict()["schema_version"] == "phase9.operational.v1"


def test_state_change_requires_both_states():
    with pytest.raises(ValueError):
        OperationalEvent(
            event_type=OperationalEventType.DEPENDENCY_STATE_CHANGED,
            workspace_id="ws_1",
            occurred_at="2026-10-05T08:00:00+00:00",
            source="connector-health",
            state=OperationalState.DEGRADED,
        )


def test_unknown_never_counts_as_safe_execution():
    for state in (
        OperationalState.UNKNOWN,
        OperationalState.UNAVAILABLE,
        OperationalState.RECONCILIATION_REQUIRED,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
        OperationalState.DEGRADED,
    ):
        assert can_execute_safely(state) is False
    assert can_execute_safely(OperationalState.HEALTHY) is True


def test_conservative_recovery_path():
    validate_transition(OperationalState.UNAVAILABLE, OperationalState.RECOVERING)
    validate_transition(OperationalState.RECOVERING, OperationalState.HEALTHY)
    validate_transition(OperationalState.UNKNOWN, OperationalState.RECONCILIATION_REQUIRED)


def test_invalid_transition_is_rejected():
    with pytest.raises(ValueError):
        validate_transition(OperationalState.HEALTHY, OperationalState.RECOVERING)


def test_security_hold_can_quarantine_but_not_directly_become_healthy():
    validate_transition(OperationalState.HEALTHY, OperationalState.SECURITY_HOLD)
    validate_transition(OperationalState.SECURITY_HOLD, OperationalState.QUARANTINED)
    with pytest.raises(ValueError):
        validate_transition(OperationalState.SECURITY_HOLD, OperationalState.HEALTHY)


def test_reconciliation_event_requires_subject():
    with pytest.raises(ValueError):
        OperationalEvent(
            event_type=OperationalEventType.RECONCILIATION_REQUIRED,
            workspace_id="ws_1",
            occurred_at="2026-10-05T08:00:00+00:00",
            source="reconciliation",
        )
