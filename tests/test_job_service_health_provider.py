import asyncio

from lifecycle import HealthStatus, _JobServiceHealthProvider


class FakeJobService:
    def __init__(self, status):
        self.status = status

    def health_check(self):
        return {"status": self.status, "detail": "test"}


def test_job_service_health_provider_preserves_explicit_status_semantics():
    expected = {
        "healthy": HealthStatus.HEALTHY,
        "ok": HealthStatus.HEALTHY,
        "degraded": HealthStatus.DEGRADED,
        "unhealthy": HealthStatus.UNHEALTHY,
        "offline": HealthStatus.UNHEALTHY,
        "unknown": HealthStatus.UNKNOWN,
        "starting": HealthStatus.UNKNOWN,
        "not_configured": HealthStatus.UNKNOWN,
    }

    for status, readiness in expected.items():
        provider = _JobServiceHealthProvider(FakeJobService(status))
        report = asyncio.run(provider.check_health_async())

        assert report.service_name == "job_service"
        assert report.status is readiness
        assert report.metadata["status"] == status


def test_job_service_health_provider_defaults_missing_status_to_unknown():
    class MissingStatus:
        def health_check(self):
            return {"detail": "provider did not report readiness"}

    report = asyncio.run(
        _JobServiceHealthProvider(MissingStatus()).check_health_async()
    )

    assert report.status is HealthStatus.UNKNOWN
