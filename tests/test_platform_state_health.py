from datetime import datetime, timedelta, timezone

from core.platform_state import Readiness
from core.platform_state_health import (
    build_platform_state_from_health_reports,
    read_platform_state_from_health_registry,
)


NOW = datetime(2026, 11, 11, 14, 30, tzinfo=timezone.utc)


class Report:
    def __init__(self, service_name, status, checked_at=NOW, correlation_id="corr-1", metadata=None):
        self.service_name = service_name
        self.status = status
        self.checked_at = checked_at
        self.correlation_id = correlation_id
        self.metadata = metadata or {}


class FakeRegistry:
    def __init__(self, reports):
        self.reports = reports
        self.probe_calls = 0

    def read_snapshot(self):
        return self.reports

    async def check_all(self):
        self.probe_calls += 1
        raise AssertionError("cached Platform State adapter must not trigger probes")


def test_maps_owner_named_health_reports_and_preserves_evidence():
    reports = {
        "director": Report("director", "healthy", metadata={"version": "v5.5"}),
    }
    snapshot = build_platform_state_from_health_reports(
        reports,
        now=NOW,
        dependencies={"director": ()},
    )
    service = snapshot.services[0]
    assert service.readiness is Readiness.HEALTHY
    assert service.owner == "director"
    assert service.source_version == "v5.5"
    assert service.evidence[0].source == "health_registry:director"
    assert service.evidence[0].correlation_id == "corr-1"


def test_missing_health_report_remains_unknown():
    snapshot = build_platform_state_from_health_reports(
        {},
        now=NOW,
        dependencies={"director": ()},
    )
    assert snapshot.services[0].readiness is Readiness.UNKNOWN
    assert snapshot.services[0].reason == "no_owner_observation"


def test_unhealthy_health_report_maps_to_unavailable():
    snapshot = build_platform_state_from_health_reports(
        {"database": Report("database", "unhealthy")},
        now=NOW,
        dependencies={"database": ()},
    )
    assert snapshot.services[0].readiness is Readiness.UNAVAILABLE


def test_stale_health_report_is_not_healthy():
    snapshot = build_platform_state_from_health_reports(
        {"director": Report("director", "healthy", NOW - timedelta(minutes=8))},
        now=NOW,
        stale_after=timedelta(minutes=5),
        dependencies={"director": ()},
    )
    assert snapshot.services[0].readiness is Readiness.STALE


def test_wrong_provider_identity_is_ignored():
    snapshot = build_platform_state_from_health_reports(
        {"director": Report("meta_engine", "healthy")},
        now=NOW,
        dependencies={"director": ()},
    )
    assert snapshot.services[0].readiness is Readiness.UNKNOWN


def test_registry_reader_uses_cache_without_invoking_health_probes():
    registry = FakeRegistry({"director": Report("director", "healthy")})
    snapshot = read_platform_state_from_health_registry(
        registry=registry,
        now=NOW,
    )
    assert snapshot.services[0].readiness is Readiness.HEALTHY
    assert registry.probe_calls == 0
