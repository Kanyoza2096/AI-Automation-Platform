import asyncio
from enum import Enum

import pytest

from lifecycle import HealthStatus, _JobServiceHealthProvider


class _JobService:
    def __init__(self, status_marker=True, status=None):
        self.status_marker = status_marker
        self.status = status

    def health_check(self):
        if not self.status_marker:
            return {"reason": "status omitted"}
        return {"status": self.status, "reason": "test"}


class _Status(Enum):
    READY = "ready"


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("healthy", HealthStatus.HEALTHY),
        ("ok", HealthStatus.HEALTHY),
        ("nominal", HealthStatus.HEALTHY),
        ("ready", HealthStatus.HEALTHY),
        ("running", HealthStatus.HEALTHY),
        ("unknown", HealthStatus.UNKNOWN),
        ("not_configured", HealthStatus.UNKNOWN),
        ("starting", HealthStatus.UNKNOWN),
        ("unhealthy", HealthStatus.UNHEALTHY),
        ("offline", HealthStatus.UNHEALTHY),
        ("failed", HealthStatus.UNHEALTHY),
        ("critical", HealthStatus.UNHEALTHY),
        ("stopped", HealthStatus.UNHEALTHY),
        ("degraded", HealthStatus.DEGRADED),
    ],
)
def test_job_service_health_provider_preserves_reported_status(reported, expected):
    provider = _JobServiceHealthProvider(_JobService(status=reported))

    report = asyncio.run(provider.check_health_async())

    assert report.service_name == "job_service"
    assert report.status is expected
    assert report.metadata["status"] == reported


def test_missing_status_is_unknown_not_healthy():
    report = asyncio.run(_JobServiceHealthProvider(_JobService(status_marker=False)).check_health_async())

    assert report.status is HealthStatus.UNKNOWN


def test_enum_and_whitespace_status_values_are_normalized():
    report = asyncio.run(_JobServiceHealthProvider(_JobService(status=_Status.READY)).check_health_async())

    assert report.status is HealthStatus.HEALTHY
