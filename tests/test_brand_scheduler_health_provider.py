import asyncio

from lifecycle import HealthStatus, _BrandSchedulerHealthProvider


class FakeBrandSchedulerManager:
    def __init__(self, running=True, status="healthy"):
        self._running = running
        self._status = status

    def health_check(self):
        return {"status": self._status, "stats": {"brands_active": 2}}


def test_brand_scheduler_health_provider_uses_canonical_identity():
    provider = _BrandSchedulerHealthProvider(FakeBrandSchedulerManager())

    assert provider.service_name == "brand_scheduler"


def test_brand_scheduler_health_provider_preserves_healthy_and_degraded_states():
    healthy = _BrandSchedulerHealthProvider(FakeBrandSchedulerManager(status="healthy"))
    degraded = _BrandSchedulerHealthProvider(FakeBrandSchedulerManager(status="degraded"))

    healthy_report = asyncio.run(healthy.check_health_async())
    degraded_report = asyncio.run(degraded.check_health_async())

    assert healthy_report.status is HealthStatus.HEALTHY
    assert healthy_report.service_name == "brand_scheduler"
    assert degraded_report.status is HealthStatus.DEGRADED


def test_brand_scheduler_health_provider_does_not_promote_idle_or_stopped_to_healthy():
    idle = _BrandSchedulerHealthProvider(FakeBrandSchedulerManager(status="idle"))
    stopped = _BrandSchedulerHealthProvider(FakeBrandSchedulerManager(running=False))

    assert asyncio.run(idle.check_health_async()).status is HealthStatus.UNKNOWN
    assert asyncio.run(stopped.check_health_async()).status is HealthStatus.UNKNOWN
