"""Correlation ID propagation tests for the unified event bus."""

import asyncio

from core.unified_bus import UnifiedEventBus
from utils.ids import get_current_correlation_id


def test_event_handler_correlation_id_is_scoped_and_reset():
    bus = UnifiedEventBus()
    observed = []

    async def handler(event_data):
        observed.append(get_current_correlation_id())

    asyncio.run(bus._invoke_handler(
        handler,
        "engine_alert",
        {"engine": "job_service"},
        "test",
        "event-123",
        workspace_id="platform",
        correlation_id="corr-123",
    ))

    assert observed == ["corr-123"]
    assert get_current_correlation_id() is None


def test_event_id_is_used_when_correlation_id_is_missing():
    bus = UnifiedEventBus()
    observed = []

    async def handler(event_data):
        observed.append(get_current_correlation_id())

    asyncio.run(bus._invoke_handler(
        handler,
        "engine_alert",
        {"engine": "job_service"},
        "test",
        "event-fallback-123",
    ))

    assert observed == ["event-fallback-123"]
    assert get_current_correlation_id() is None
