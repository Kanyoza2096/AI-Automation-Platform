import asyncio

import pytest

from core.event_bus import EventBusBridge


class FakeUnifiedBus:
    def __init__(self):
        self.published = []
        self.subscriptions = {}

    async def publish(self, event_name, payload=None, source="unknown", **kwargs):
        self.published.append((event_name, payload, source, kwargs))
        return "evt_test"

    async def subscribe(self, event_name, handler):
        self.subscriptions.setdefault(event_name, []).append(handler)

        def unsubscribe():
            self.subscriptions[event_name] = [
                h for h in self.subscriptions[event_name] if h is not handler
            ]

        return unsubscribe

    def health_check(self):
        return {"status": "healthy"}


@pytest.mark.asyncio
async def test_bridge_publishes_once_to_unified_bus():
    unified = FakeUnifiedBus()
    bridge = EventBusBridge(unified=unified)

    result = await bridge.publish(
        "post.published",
        {"workspace_id": "ws-1"},
        source="test",
        workspace_id="ws-1",
        correlation_id="corr-1",
    )

    assert result == "evt_test"
    assert len(unified.published) == 1
    assert unified.published[0][0] == "post.published"
    assert unified.published[0][3]["workspace_id"] == "ws-1"
    assert bridge._legacy.get_stats().get("event_count", 0) == 0


@pytest.mark.asyncio
async def test_bridge_subscription_uses_unified_only():
    unified = FakeUnifiedBus()
    bridge = EventBusBridge(unified=unified)
    received = []

    unsubscribe = bridge.subscribe("post.published", lambda event: received.append(event))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "post.published" in unified.subscriptions
    assert bridge._legacy.get_stats().get("event_count", 0) == 0

    unified.subscriptions["post.published"][0]({"id": "p1"}, source="test")
    assert len(received) == 1
    assert getattr(received[0], "payload", None) == {"id": "p1"}

    unsubscribe()
    assert unified.subscriptions["post.published"] == []


def test_bridge_health_reports_authoritative_transport():
    unified = FakeUnifiedBus()
    bridge = EventBusBridge(unified=unified)

    health = bridge.health_check()

    assert health["transport"] == "UnifiedEventBus"
    assert health["compatibility_bridge"] is True
