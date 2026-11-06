"""Health monitor emits bounded per-provider state for actionable diagnostics."""

import logging

import pytest

from services.health_monitor_service import HealthMonitorService


@pytest.mark.asyncio
async def test_health_cycle_logs_service_names_for_down_and_unknown(caplog, monkeypatch):
    monitor = HealthMonitorService()
    snapshot = {
        "healthy_provider": "ok",
        "down_provider": "down",
        "unknown_provider": "unknown",
    }

    async def collect():
        return snapshot

    monkeypatch.setattr(monitor, "_collect_service_health", collect)

    with caplog.at_level(logging.INFO, logger="kanyoza.services.health_monitor"):
        await monitor._check_all_services()

    record = next(
        item for item in caplog.records
        if getattr(item, "event", None) == "health_monitor.cycle.completed"
    )
    assert record.service_states == snapshot
    assert record.services_down_names == ["down_provider"]
    assert record.services_unknown_names == ["unknown_provider"]
    assert record.services_total == 3
    assert record.services_ok == 1



def test_plugin_health_classification_preserves_unknown_and_legacy_states():
    classify = HealthMonitorService._classify_plugin_health

    assert classify({"ok": False, "status": "unknown"}) == "unknown"
    assert classify({"ok": False, "status": "starting"}) == "unknown"
    assert classify({"ok": True, "status": "healthy"}) == "ok"
    assert classify({"ok": False, "status": "unhealthy"}) == "down"
    assert classify({"ok": True}) == "ok"
    assert classify({"ok": False}) == "down"



@pytest.mark.asyncio
async def test_meta_engine_async_health_provider_preserves_engine_diagnostics():
    from services.meta_engine.core import MetaEngineV7

    expected = {
        "status": "unknown",
        "snapshot_status": "unknown",
        "engine_states": {"alpha": "nominal", "beta": "unknown"},
        "unknown_engines": ["beta"],
        "degraded_engines": [],
        "offline_engines": [],
    }

    class FakeMetaEngine:
        def check_health(self):
            return expected

    result = await MetaEngineV7.check_health_async(FakeMetaEngine())
    assert result == expected
