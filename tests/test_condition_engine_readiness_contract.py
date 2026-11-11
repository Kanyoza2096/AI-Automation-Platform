"""Regression tests for explicit Condition Engine capability readiness."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from core.platform_state import Readiness
from services.condition_engine.core import AutonomousConditionEngine
from services.condition_engine.models import EngineConfig


def test_analysis_can_be_ready_while_execution_is_unavailable():
    engine = AutonomousConditionEngine(config=EngineConfig(dry_run=True))
    health = engine.check_health()
    assert health["status"] == "healthy"
    assert health["readiness"]["analysis_ready"] is True
    assert health["readiness"]["execution_ready"] is False
    assert "action_executor_unavailable" in health["readiness"]["limitations"]


def test_callable_executor_is_not_proof_of_governed_binding_or_persistence():
    engine = AutonomousConditionEngine(
        config=EngineConfig(dry_run=True),
        action_executor=lambda plan: {"ok": True},
        supabase_client=object(),
    )
    readiness = engine.readiness_report()
    assert readiness["execution_ready"] is True
    assert readiness["executor_binding"] == "unverified"
    assert readiness["persistence_configured"] is True
    assert readiness["persistence_ready"] is False
    assert readiness["persistence_status"] == "unknown"


def test_binding_status_only_accepts_known_values():
    engine = AutonomousConditionEngine(config=EngineConfig(dry_run=True))
    engine.set_executor_binding_status("bound")
    assert engine.readiness_report()["executor_binding"] == "bound"
    engine.set_executor_binding_status("made_up")
    assert engine.readiness_report()["executor_binding"] == "unverified"


def test_api_health_uses_core_readiness_instead_of_hardcoded_green():
    from services.condition_engine import api

    result = api.health_check()
    assert result["status"] == api.autonomous_engine.check_health()["status"]
    assert result["readiness"]["analysis_ready"] is True
    assert result["readiness"]["execution_ready"] is False


class FakePlatformStateReader:
    def __init__(self, snapshot=None, error=None):
        self.snapshot = snapshot
        self.error = error
        self.calls = 0

    async def get_snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.snapshot


def _shared_snapshot(updated_at=None):
    updated_at = updated_at or datetime.now(timezone.utc)
    return SimpleNamespace(
        revision=12,
        schema_version=1,
        updated_at=updated_at,
        services=(
            SimpleNamespace(
                service_id="director",
                owner="director",
                readiness=Readiness.HEALTHY,
                reason=None,
            ),
            SimpleNamespace(
                service_id="condition_engine",
                owner="condition_engine",
                readiness=Readiness.DEGRADED,
                reason="dependency_degraded",
            ),
        ),
        capabilities=(
            SimpleNamespace(
                capability_id="condition_analysis",
                owner_service="condition_engine",
                readiness=Readiness.DEGRADED,
            ),
        ),
    )


def test_async_health_exposes_shared_platform_state_without_changing_local_readiness():
    reader = FakePlatformStateReader(_shared_snapshot())
    engine = AutonomousConditionEngine(platform_state_reader=reader)

    health = asyncio.run(engine.check_health_async())

    assert reader.calls == 1
    assert health["platform_state"]["status"] == "available"
    assert health["platform_state"]["revision"] == 12
    assert health["platform_state"]["services"][1]["readiness"] == "degraded"
    assert health["platform_state"]["policy"]["read_only"] is True
    assert health["platform_state"]["policy"]["grants_execution_authority"] is False
    assert health["readiness"]["analysis_ready"] is True
    assert health["readiness"]["execution_ready"] is False


def test_condition_engine_downgrades_expired_platform_readiness_to_stale():
    reader = FakePlatformStateReader(
        _shared_snapshot(datetime.now(timezone.utc) - timedelta(minutes=10))
    )
    engine = AutonomousConditionEngine(platform_state_reader=reader)

    state = asyncio.run(engine.refresh_platform_state_context(max_age_seconds=60))

    assert state["status"] == "stale"
    assert state["services"][0]["readiness"] == "stale"
    assert state["services"][0]["reported_readiness"] == "healthy"


def test_condition_engine_shared_state_read_failure_is_unknown_not_healthy():
    reader = FakePlatformStateReader(error=RuntimeError("read failed"))
    engine = AutonomousConditionEngine(platform_state_reader=reader)

    state = asyncio.run(engine.refresh_platform_state_context())

    assert state["status"] == "unavailable"
    assert state["freshness"] == "unknown"
    assert state["services"] == []


def test_condition_engine_lifecycle_supabase_injection_configures_platform_state_reader(monkeypatch):
    reader = FakePlatformStateReader(_shared_snapshot())
    monkeypatch.setattr(
        "core.platform_state_reader.PlatformStateReader",
        lambda _client: reader,
    )
    engine = AutonomousConditionEngine()
    engine.set_supabase_client(object())

    state = asyncio.run(engine.refresh_platform_state_context())

    assert state["status"] == "available"
    assert state["revision"] == 12
