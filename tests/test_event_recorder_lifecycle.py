from __future__ import annotations

import pytest

from core.events import DomainEvent, EventType
from domain.analytics.event_recorder import EventRecorder


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


class FakeBus:
    def __init__(self, fail_at: int | None = None):
        self.subscribers = {}
        self.calls = 0
        self.fail_at = fail_at

    def subscribe(self, event_type, handler):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("subscription failed")
        self.subscribers.setdefault(event_type, []).append(handler)

        def unsubscribe():
            self.subscribers[event_type] = [
                item for item in self.subscribers[event_type] if item is not handler
            ]

        return unsubscribe

    def emit(self, event_type, payload):
        event = DomainEvent(event_type=event_type, payload=payload)
        for handler in tuple(self.subscribers.get(event_type, [])):
            handler(event)


def test_start_registers_all_subscriptions_and_is_idempotent():
    bus, repo = FakeBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    recorder.start()
    recorder.start()

    assert recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 5

    bus.emit(EventType.POST_PUBLISHED, {"topic": "KDO", "platform": "x", "post_id": "p1"})
    bus.emit(EventType.POST_GENERATED, {"topic": "KDO", "provider": "test"})
    bus.emit(EventType.SCAN_COMPLETED, {"scan_id": "s1"})
    bus.emit(EventType.MESSAGE_RECEIVED, {"sender_id": "u1"})
    bus.emit(EventType.MESSAGE_SENT, {"sender_id": "u2"})

    assert repo.posts == [{"topic": "KDO", "platform": "x", "post_id": "p1"}]
    assert [item["event"] for item in repo.events] == ["post_generated", "scan_completed"]
    assert repo.messages == [
        {"sender_id": "u1", "direction": "inbound"},
        {"sender_id": "u2", "direction": "outbound"},
    ]


def test_stop_removes_all_subscriptions_and_is_idempotent():
    bus, repo = FakeBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)
    recorder.start()

    recorder.stop()
    recorder.stop()

    assert not recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 0


def test_start_stop_start_does_not_duplicate_delivery():
    bus, repo = FakeBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    recorder.start()
    recorder.stop()
    recorder.start()
    bus.emit(EventType.POST_PUBLISHED, {"post_id": "p2"})

    assert len(repo.posts) == 1
    assert sum(map(len, bus.subscribers.values())) == 5


def test_partial_subscription_failure_rolls_back_prior_subscriptions():
    bus, repo = FakeBus(fail_at=3)
    recorder = EventRecorder(repo, bus)

    with pytest.raises(RuntimeError, match="subscription failed"):
        recorder.start()

    assert not recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 0


def test_non_callable_unsubscribe_is_rejected_and_rolled_back():
    class BrokenBus(FakeBus):
        def subscribe(self, event_type, handler):
            super().subscribe(event_type, handler)
            return None

    bus, repo = BrokenBus(), FakeRepository()
    recorder = EventRecorder(repo, bus)

    with pytest.raises(RuntimeError, match="unsubscribe callback"):
        recorder.start()

    assert not recorder.is_started
    assert sum(map(len, bus.subscribers.values())) == 0
