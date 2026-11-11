from datetime import datetime, timedelta, timezone

import pytest

from core.platform_state import (
    EvidenceRef,
    Readiness,
    ServiceManifest,
    ServiceObservation,
    build_platform_state,
)


NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def manifest(service_id, *, owner=None, dependencies=(), capabilities=()):
    return ServiceManifest(
        service_id=service_id,
        owner=owner or service_id,
        dependencies=tuple(dependencies),
        capabilities=tuple(capabilities),
    )


def observation(service_id, readiness=Readiness.HEALTHY, *, owner=None, observed_at=NOW):
    return ServiceObservation(
        service_id=service_id,
        owner=owner or service_id,
        readiness=readiness,
        observed_at=observed_at,
        source_version="test-v1",
        evidence=(EvidenceRef("test", observed_at, "test-v1"),),
    )


def test_builds_versioned_snapshot_and_capability_readiness():
    manifests = [
        manifest("database", capabilities=("database.read",)),
        manifest("director", dependencies=("database",), capabilities=("director.plan",)),
    ]
    snapshot = build_platform_state(
        manifests,
        [observation("database"), observation("director")],
        previous_revision=4,
        now=NOW,
    )
    assert snapshot.revision == 5
    assert snapshot.services[1].service_id == "director"
    assert snapshot.services[1].readiness is Readiness.HEALTHY
    assert {item.capability_id: item.readiness for item in snapshot.capabilities} == {
        "database.read": Readiness.HEALTHY,
        "director.plan": Readiness.HEALTHY,
    }


def test_registration_without_observation_is_unknown():
    snapshot = build_platform_state([manifest("director")], [], now=NOW)
    assert snapshot.services[0].readiness is Readiness.UNKNOWN
    assert snapshot.services[0].reason == "no_owner_observation"


def test_stale_observation_is_not_healthy():
    old = NOW - timedelta(minutes=6)
    snapshot = build_platform_state(
        [manifest("meta")], [observation("meta", observed_at=old)],
        now=NOW, stale_after=timedelta(minutes=5),
    )
    assert snapshot.services[0].readiness is Readiness.STALE
    assert snapshot.services[0].reason == "observation_expired"


def test_unavailable_dependency_propagates_to_dependent_service():
    manifests = [
        manifest("database"),
        manifest("director", dependencies=("database",), capabilities=("director.plan",)),
    ]
    snapshot = build_platform_state(
        manifests,
        [observation("database", Readiness.UNAVAILABLE), observation("director")],
        now=NOW,
    )
    director = next(item for item in snapshot.services if item.service_id == "director")
    capability = snapshot.capabilities[0]
    assert director.readiness is Readiness.UNAVAILABLE
    assert director.reason == "dependency_unavailable"
    assert capability.readiness is Readiness.UNAVAILABLE


def test_observation_must_come_from_manifest_owner():
    with pytest.raises(ValueError, match="owner mismatch"):
        build_platform_state(
            [manifest("director", owner="director-team")],
            [observation("director", owner="untrusted-producer")],
            now=NOW,
        )


def test_undeclared_dependency_is_rejected():
    with pytest.raises(ValueError, match="undeclared dependencies"):
        build_platform_state(
            [manifest("director", dependencies=("database",))], [], now=NOW
        )


def test_dependency_cycle_is_rejected():
    manifests = [
        manifest("director", dependencies=("meta",)),
        manifest("meta", dependencies=("director",)),
    ]
    with pytest.raises(ValueError, match="cyclic service dependencies"):
        build_platform_state(manifests, [], now=NOW)


def test_snapshot_scope_and_timestamp_are_validated():
    with pytest.raises(ValueError, match="scope_id"):
        build_platform_state(
            [manifest("director")], [], now=NOW,
            scope_type="workspace", scope_id=" ",
        )


def test_future_observation_is_unknown_not_healthy():
    future = NOW + timedelta(minutes=10)
    snapshot = build_platform_state(
        [manifest("director")],
        [observation("director", observed_at=future)],
        now=NOW,
    )
    assert snapshot.services[0].readiness is Readiness.UNKNOWN
    assert snapshot.services[0].reason == "observation_from_future"
