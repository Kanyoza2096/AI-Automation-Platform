"""Regression tests for UnifiedEventBus envelope consumers."""

import asyncio

from services.workforce.consumers.escalation_consumer import EscalationConsumer
from services.workforce.consumers.heal_command_consumer import HealCommandConsumer


class FakeBus:
    def __init__(self):
        self.published = []

    async def publish(self, *args, **kwargs):
        self.published.append((args, kwargs))


def test_escalation_consumer_preserves_engine_from_event_envelope():
    bus = FakeBus()
    consumer = EscalationConsumer(bus=bus)

    asyncio.run(consumer._on_escalation(event={
        "event_name": "escalation_request",
        "source": "autonomous_loop",
        "correlation_id": "corr-123",
        "workspace_id": "platform",
        "payload": {
            "engine": "job_service",
            "reason": "health check degraded",
        },
    }))

    assert consumer.get_status()["events_handled"] == 1
    assert len(bus.published) == 1
    event_name, event_payload = bus.published[0][0]
    assert event_name == "operator.task.created"
    assert event_payload["engine"] == "job_service"
    assert event_payload["correlation_id"] == "corr-123"
    assert bus.published[0][1]["correlation_id"] == "corr-123"


def test_heal_consumer_dispatches_only_explicitly_mapped_engine():
    bus = FakeBus()
    consumer = HealCommandConsumer(bus=bus)
    calls = []

    async def fake_dispatch(command_id, body):
        calls.append((command_id, body))
        return {"transaction_id": "txn-1"}

    consumer._dispatch = fake_dispatch
    event = {
        "event_name": "heal_command",
        "correlation_id": "corr-456",
        "payload": {
            "engine": "scheduler",
            "reason": "service degraded",
            "workspace_id": "platform",
        },
    }
    asyncio.run(consumer._on_heal_command(event=event))

    assert calls == [(
        "scheduler.restart",
        {
            "target": "scheduler",
            "reason": "service degraded",
            "workspace_id": "platform",
            "correlation_id": "corr-456",
            "requested_by": "autonomous_loop",
        },
    )]
    assert consumer.get_status()["events_handled"] == 1


def test_heal_consumer_ignores_job_queue_health_without_restart_mapping():
    consumer = HealCommandConsumer(bus=FakeBus())
    calls = []

    async def fake_dispatch(command_id, body):
        calls.append((command_id, body))
        return {"transaction_id": "must-not-exist"}

    consumer._dispatch = fake_dispatch
    asyncio.run(consumer._on_heal_command(event={
        "event_name": "heal_command",
        "payload": {"engine": "job_stats_service", "reason": "failed jobs"},
    }))

    assert calls == []
    assert consumer.get_status()["events_handled"] == 0
    assert consumer.get_status()["events_ignored"] == 1


def test_heal_consumer_throttles_duplicate_engine_alerts():
    consumer = HealCommandConsumer(bus=FakeBus())
    calls = []

    async def fake_dispatch(command_id, body):
        calls.append(command_id)
        return {"transaction_id": "txn-1"}

    consumer._dispatch = fake_dispatch
    event = {"event_name": "heal_command", "payload": {"engine": "scheduler"}}
    asyncio.run(consumer._on_heal_command(event=event))
    asyncio.run(consumer._on_heal_command(event=event))

    assert calls == ["scheduler.restart"]
    assert consumer.get_status()["events_handled"] == 1
    assert consumer.get_status()["events_ignored"] == 1


def test_heal_consumer_does_not_count_unaccepted_dispatch_as_handled():
    consumer = HealCommandConsumer(bus=FakeBus())

    async def fake_dispatch(command_id, body):
        return {"ok": False, "error": "approval service unavailable"}

    consumer._dispatch = fake_dispatch
    asyncio.run(consumer._on_heal_command(event={
        "event_name": "heal_command",
        "payload": {"engine": "scheduler"},
    }))

    assert consumer.get_status()["events_handled"] == 0
    assert consumer.get_status()["events_failed"] == 1
