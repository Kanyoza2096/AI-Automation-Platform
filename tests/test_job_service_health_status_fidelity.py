import asyncio

import pytest

from lifecycle import HealthStatus, _JobServiceHealthProvider


class _JobService:
    def __init__(self, status):
        self.status = status

    def health_check(self):
        return {"status": self.status, "reason": "test"}


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("healthy", HealthStatus.HEALTHY),
        ("ok", HealthStatus.HEALTHY),
        ("nominal", HealthStatus.HEALTHY),
        ("ready", HealthStatus.HEALTHY),
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
    provider = _JobServiceHealthProvider(_JobService(reported))

    report = asyncio.run(provider.check_health_async())

    assert report.service_name == "job_service"
    assert report.status is expected
    assert report.metadata["status"] == reported
