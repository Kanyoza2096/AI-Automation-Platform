from __future__ import annotations

import asyncio

from core.shared_event_bus import SharedEventBus
from core.unified_bus import UnifiedEventBus


class _Result:
    def __init__(self, data=None):
        self.data = data or []


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self._data = None
        self._range = None

    def insert(self, value):
        self.db.inserted.append((self.table, value))
        if self.table == "event_deliveries":
            key = (value["event_id"], value["consumer_group"])
            self.db.deliveries.setdefault(key, {"id": "delivery-1", **value})
        return self

    def select(self, *args): return self
    def eq(self, key, value): self.filters[key] = value; return self
    def is_(self, *args): return self
    def lte(self, *args): return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args): return self
    def range(self, start, end): self._range = (start, end); return self
    def or_(self, *args): return self

    def update(self, value):
        self.db.updates.append((self.table, value, dict(self.filters)))
        return self

    def execute(self):
        if self.table == "events":
            rows = self.db.events
            if self._range is not None:
                start, end = self._range
                rows = rows[start:end + 1]
            return _Result(rows)
        if self.table == "event_deliveries":
            rows = list(self.db.deliveries.values())
            if self.filters:
                rows = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
            return _Result(rows)
        return _Result()


class _DB:
    def __init__(self):
        self.events = []
        self.deliveries = {}
        self.inserted = []
        self.updates = []

    def table(self, name): return _Query(self, name)
    def rpc(self, name, params):
        db = self
        class Q:
            def execute(self): return _Result(True)
        return Q()


def test_shared_bus_uses_per_consumer_delivery_rows():
    db = _DB()
    db.events = [{
        "id": "event-1",
        "event_type": "job.created",
        "payload": {"workspace_id": "ws-1"},
        "source_instance": "other:abc",
    }]
    bus = SharedEventBus(db, source_service="director")
    seen = []
    bus.subscribe("job.created", lambda payload: seen.append(payload))

    assert bus.poll() == 1
    assert seen == [{"workspace_id": "ws-1"}]
    assert ("event-1", "director") in db.deliveries
    assert db.deliveries[("event-1", "director")]["processed"] is False


def test_shared_bus_does_not_self_consume_own_transport_copy():
    db = _DB()
    bus = SharedEventBus(db, source_service="workers")
    db.events = [{
        "id": "event-1",
        "event_type": "job.created",
        "payload": {},
        "source_instance": bus.instance_id,
    }]
    seen = []
    bus.subscribe("job.created", lambda payload: seen.append(payload))
    assert bus.poll() == 0
    assert seen == []


def test_unified_bus_persistence_uses_events_schema_and_tenant_metadata():
    class DB:
        def __init__(self): self.records = None
        def table(self, name):
            assert name == "events"
            db = self
            class Q:
                def upsert(self, records, on_conflict=None): db.records = records; return self
                def execute(self): return _Result([{}])
            return Q()

    async def run():
        db = DB()
        bus = UnifiedEventBus(db)
        bus._running = True
        event_id = await bus.publish(
            "publish.requested",
            {"x": 1},
            source="director",
            workspace_id="ws-1",
            correlation_id="corr-1",
            decision_id="dec-1",
        )
        await bus._flush_persistence()
        return event_id, db.records

    event_id, records = asyncio.run(run())
    assert records[0]["id"] == event_id
    assert records[0]["event_type"] == "publish.requested"
    assert records[0]["workspace_id"] == "ws-1"
    assert records[0]["correlation_id"] == "corr-1"
    assert records[0]["source_service"] == "director"
