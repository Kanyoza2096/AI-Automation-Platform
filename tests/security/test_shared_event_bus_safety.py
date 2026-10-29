from __future__ import annotations

from core.shared_event_bus import SharedEventBus


def test_shared_bus_instances_have_distinct_identity():
    a = SharedEventBus(source_service="workers")
    b = SharedEventBus(source_service="workers")
    assert a.source_service == b.source_service == "workers"
    assert a.instance_id != b.instance_id


def test_shared_bus_does_not_mark_unhandled_events_processed(monkeypatch):
    class Query:
        def select(self, *args): return self
        def eq(self, *args): return self
        def is_(self, *args): return self
        def order(self, *args, **kwargs): return self
        def limit(self, *args): return self
        def execute(self):
            class R: data = [{"id": "e1", "event_type": "handled_elsewhere", "processed": False}]
            return R()

    class DB:
        def table(self, name): return Query()

    bus = SharedEventBus(DB(), source_service="workers")
    claimed = []
    monkeypatch.setattr(bus, "_claim_event", lambda event_id: claimed.append(event_id) or True)
    assert bus.poll() == 0
    assert claimed == []
