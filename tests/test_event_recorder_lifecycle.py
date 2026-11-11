from __future__ import annotations

import asyncio

import pytest

from domain.analytics.event_recorder import EventRecorder
from core.events import EventType


class FakeRepository:
    def __init__(self):
        self.posts = []
        self.events = []
        self.messages = []

    def record_post_published(self, **kwargs):
        self.posts.append(kwargs)

    def save(self, value):
        self.events.append(value)

    def record_message(self, **kwargs):
        self.messages.append(kwargs)


class FakeUnifiedBus:
    def __init__(self, fail_at: int | None = None):
        self.subscribers = {}
        self.calls = 0
        self.fail_at = fail_at

    async def subscribe(self, event_name, handler):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("subscription failed")
        self.subscribers.setdefault(event_name, []).append(handler)

        def unsubscribe():
            self.subscribers[event_name] = [
                item for item in self.subscribers[event_name] if item is not handler
            ]

        return unsubscribe

    def emit(self, event_type, payload, source="test"):
        for handler in tuple(self.subscribers.get(event_type.value, [])):
            handler({
                "event_name": event_type.value,
                "payload": payload,
                "source": source,
                "event_id": "event-1",
            })


def test_start_awaits_all_subscriptions_and_is_idempotent():
    bus, repo = FakeUnifiedBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    async def run():
        await recorder.start()
        await recorder.start()

    asyncio.run(run())

    assert recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 4

    bus.emit(EventType.POST_GENERATED, {"topic": "KDO", "provider": "test"})
    bus.emit(EventType.SCAN_COMPLETED, {"scan_id": "s1"})
    bus.emit(EventType.MESSAGE_RECEIVED, {"sender_id": "u1"})
    bus.emit(EventType.MESSAGE_SENT, {"sender_id": "u2"})

    assert [item["event"] for item in repo.events] == ["post_generated", "scan_completed"]
    assert repo.messages == [
        {"sender_id": "u1", "direction": "inbound"},
        {"sender_id": "u2", "direction": "outbound"},
    ]


def test_stop_removes_all_subscriptions_and_is_idempotent():
    bus, repo = FakeUnifiedBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    async def run():
        await recorder.start()
        await recorder.stop()
        await recorder.stop()

    asyncio.run(run())

    assert not recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 0


def test_start_stop_start_does_not_duplicate_delivery():
    bus, repo = FakeUnifiedBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    async def run():
        await recorder.start()
        await recorder.stop()
        await recorder.start()

    asyncio.run(run())
    bus.emit(EventType.POST_PUBLISHED, {"post_id": "p2"})

    assert sum(map(len, bus.subscribers.values())) == 4


def test_partial_subscription_failure_rolls_back_prior_subscriptions():
    bus, repo = FakeUnifiedBus(fail_at=3)
    recorder = EventRecorder(repo, bus)

    with pytest.raises(RuntimeError, match="subscription failed"):
        asyncio.run(recorder.start())

    assert not recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 0


def test_non_callable_unsubscribe_is_rejected_and_rolled_back():
    class BrokenBus(FakeUnifiedBus):
        async def subscribe(self, event_name, handler):
            return None

    bus, repo = BrokenBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    with pytest.raises(RuntimeError, match="unsubscribe callback"):
        asyncio.run(recorder.start())

    assert not recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 0


def test_publish_events_are_not_recorded_twice_by_event_recorder():
    bus, repo = FakeUnifiedBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    async def run():
        await recorder.start()

    asyncio.run(run())
    bus.emit(EventType.POST_PUBLISHED, {"topic": "already-recorded", "post_id": "p3"})

    assert repo.posts == []
    assert "post.published" not in bus.subscribers
    assert "post_published" not in bus.subscribers
