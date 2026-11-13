"""Condition Engine's read-only Platform State consumer contract."""

import asyncio
from datetime import datetime, timezone

from core.platform_state import (
    CapabilityState,
    PlatformStateSnapshot,
    Readiness,
    ServiceState,
)
from services.condition_engine import AutonomousConditionEngine


class FakePlatformStateReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    async def get_snapshot(self):
        self.calls += 1
        return self.snapshot


def test_condition_engine_health_projection_reads_canonical_platform_state():
    now = datetime.now(timezone.utc)
    snapshot = PlatformStateSnapshot(
        revision=12,
        updated_at=now,
        services=(
            ServiceState(
                service_id="job_service",
                owner="job_service",
                readiness=Readiness.DEGRADED,
                observed_at=now,
                source_version="health-report-v1",
                dependencies=("supabase", "event_bus"),
                capabilities=("job_lifecycle_management",),
                critical=True,
                reason="dependency_degraded",
            ),
        ),
        capabilities=(
            CapabilityState(
                capability_id="job_lifecycle_management",
                owner_service="job_service",
                readiness=Readiness.DEGRADED,
                observed_at=now,
                reason="dependency_degraded",
            ),
        ),
    )
    reader = FakePlatformStateReader(snapshot)
    engine = AutonomousConditionEngine(platform_state_reader=reader)

    health = asyncio.run(engine.check_health_async())

    assert reader.calls == 1
    assert health["platform_state"]["status"] == "available"
    assert health["platform_state"]["revision"] == 12
    assert health["platform_state"]["services"][0]["service_id"] == "job_service"
    assert health["platform_state"]["services"][0]["readiness"] == "degraded"
    assert health["platform_state"]["capabilities"][0]["capability_id"] == "job_lifecycle_management"
    assert health["platform_state"]["policy"]["read_only"] is True
    assert health["platform_state"]["policy"]["grants_execution_authority"] is False
