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
