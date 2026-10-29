from datetime import datetime, timedelta, timezone
import pytest

from services.phase9_reliability import (
    DependencyHealth,
    DependencyHealthObservation,
    DependencyRegistry,
    DependencyState,
    HealthEvidenceType,
    can_execute_safely,
)


BASE = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


def obs(**kw):
    data = dict(
        workspace_id="ws_1",
        dependency_id="stripe",
        state=DependencyState.HEALTHY,
        observed_at=BASE,
        evidence_type=HealthEvidenceType.CONNECTOR_CHECK,
        evidence_ref="check:1",
        correlation_id="corr:1",
    )
    data.update(kw)
    return DependencyHealthObservation(**data)


def health(state=DependencyState.HEALTHY, **kw):
    data = dict(
        workspace_id="ws_1",
        dependency_id="stripe",
        state=state,
        last_observed_at=BASE,
        evidence_ref="check:1",
        reason="initial",
    )
    data.update(kw)
    return DependencyHealth(**data)


def test_observation_requires_workspace_and_dependency():
    with pytest.raises(ValueError):
        obs(workspace_id=" ")
    with pytest.raises(ValueError):
        obs(dependency_id=" ")


def test_health_is_authority_free():
    with pytest.raises(ValueError):
        obs(authority="executor")
    with pytest.raises(ValueError):
        health(authority="operator")


@pytest.mark.parametrize("state", list(DependencyState))
def test_all_operational_states_are_explicit(state):
    item = obs(state=state)
    assert item.state == state


def test_healthy_is_only_strict_safe_state():
    for state in DependencyState:
        item = health(state)
        assert can_execute_safely(
            item, requires_authoritative_dependency=True
        ) == (state == DependencyState.HEALTHY)


def test_security_hold_and_quarantine_are_never_safe():
    for state in (
        DependencyState.SECURITY_HOLD,
        DependencyState.QUARANTINED,
        DependencyState.UNKNOWN,
        DependencyState.UNAVAILABLE,
    ):
        assert not can_execute_safely(health(state))


def test_health_update_is_immutable_and_revisioned():
    old = health()
    new = old.apply(obs(
        state=DependencyState.DEGRADED,
        observed_at=BASE + timedelta(minutes=1),
        evidence_ref="incident:i1",
        reason="provider latency",
    ))
    assert old.state == DependencyState.HEALTHY
    assert new.state == DependencyState.DEGRADED
    assert new.revision == old.revision + 1


def test_stale_observation_cannot_overwrite_newer_state():
    old = health()
    newer = old.apply(obs(
        state=DependencyState.DEGRADED,
        observed_at=BASE + timedelta(minutes=2),
    ))
    with pytest.raises(ValueError):
        newer.apply(obs(
            state=DependencyState.HEALTHY,
            observed_at=BASE + timedelta(minutes=1),
        ))


def test_cross_workspace_update_rejected():
    with pytest.raises(ValueError):
        health().apply(obs(workspace_id="ws_2"))


def test_cross_dependency_update_rejected():
    with pytest.raises(ValueError):
        health().apply(obs(dependency_id="github"))


def test_registry_is_workspace_scoped():
    registry = DependencyRegistry("ws_1")
    updated = registry.upsert(health(DependencyState.DEGRADED))
    assert updated.get("stripe").state == DependencyState.DEGRADED
    with pytest.raises(ValueError):
        registry.upsert(health(workspace_id="ws_2"))


def test_optional_non_authoritative_safety_mode_still_blocks_hard_failures():
    assert can_execute_safely(
        health(DependencyState.DEGRADED),
        requires_authoritative_dependency=False,
    )
    assert not can_execute_safely(
        health(DependencyState.SECURITY_HOLD),
        requires_authoritative_dependency=False,
    )


def test_evidence_and_correlation_are_preserved():
    item = obs(
        state=DependencyState.RECONCILIATION_REQUIRED,
        evidence_ref="reconcile:r7",
        correlation_id="corr:r7",
        reason="drift detected",
    )
    data = item.to_dict()
    assert data["evidence_ref"] == "reconcile:r7"
    assert data["correlation_id"] == "corr:r7"
    assert data["state"] == "RECONCILIATION_REQUIRED"


def test_observation_does_not_grant_execution_authority():
    item = obs(state=DependencyState.HEALTHY)
    assert item.authority == "none"
    assert not hasattr(item, "execute")
