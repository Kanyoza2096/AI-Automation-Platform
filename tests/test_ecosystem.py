from datetime import datetime, timedelta, timezone

import pytest

from organism.ecosystem import (
    CoordinationStatus,
    EcosystemCoordinator,
    EcosystemPolicy,
    OrganismSignal,
    SignalKind,
)


def policy():
    return EcosystemPolicy("shared", frozenset({"a", "b"}))


def signal(**kwargs):
    base = dict(ecosystem_id="shared", source_workspace_id="a", target_workspace_id="b", kind=SignalKind.OPPORTUNITY, subject="topic", value="high")
    base.update(kwargs)
    return OrganismSignal(**base)


def test_explicit_membership_accepts_bounded_signal():
    c = EcosystemCoordinator()
    c.register(policy())
    result = c.propose(signal())
    assert result.status is CoordinationStatus.ACCEPTED
    assert len(c.signals_for("shared", "b")) == 1


def test_unregistered_or_nonmember_is_rejected():
    c = EcosystemCoordinator()
    result = c.propose(signal())
    assert result.status is CoordinationStatus.REJECTED
    c.register(policy())
    assert c.propose(signal(target_workspace_id="c")).status is CoordinationStatus.REJECTED
    with pytest.raises(PermissionError):
        c.signals_for("shared", "c")


def test_same_workspace_and_expired_signals_are_not_cross_organism_coordination():
    with pytest.raises(ValueError):
        signal(target_workspace_id="a")
    c = EcosystemCoordinator(); c.register(policy())
    expired = signal(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert c.propose(expired).status is CoordinationStatus.ACCEPTED
    assert c.signals_for("shared", "b") == ()


def test_signal_cannot_carry_authority_or_code_channels():
    with pytest.raises(ValueError):
        signal(metadata={"token": "x"})
    with pytest.raises(ValueError):
        signal(metadata={"handler": "danger"})


def test_allowed_kinds_and_capacity_are_enforced():
    c = EcosystemCoordinator(); c.register(EcosystemPolicy("x", frozenset({"a", "b"}), frozenset({SignalKind.RISK}), max_signals=1))
    assert c.propose(signal(ecosystem_id="x", kind=SignalKind.OPPORTUNITY)).status is CoordinationStatus.REJECTED
    assert c.propose(signal(ecosystem_id="x", kind=SignalKind.RISK)).status is CoordinationStatus.ACCEPTED
    assert c.propose(signal(ecosystem_id="x", kind=SignalKind.RISK, subject="second")).status is CoordinationStatus.REJECTED


def test_policy_cannot_silently_remove_existing_members():
    c = EcosystemCoordinator(); c.register(policy())
    with pytest.raises(ValueError):
        c.register(EcosystemPolicy("shared", frozenset({"a"})))
