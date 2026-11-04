from __future__ import annotations

from core.shared_event_bus import SharedEventBus


class Result:
    def __init__(self, data=None):
        self.data = data or []


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = {}
        self.window = None
        self.update_values = None

    def select(self, *args): return self
    def order(self, *args, **kwargs): return self
    def range(self, start, end): self.window = (start, end); return self
    def eq(self, key, value): self.filters[key] = value; return self
    def or_(self, *args): return self

    def insert(self, value):
        if self.table == "event_deliveries":
            key = (value["event_id"], value["consumer_group"])
            self.db.deliveries.setdefault(key, {"id": f"d-{value['event_id']}", **value})
        return self

    def update(self, value):
        self.update_values = value
        self.db.updates.append((self.table, value))
        return self

    def execute(self):
        if self.table == "events":
            rows = self.db.events
            if self.window:
                start, end = self.window
                rows = rows[start:end + 1]
            return Result(rows)
        if self.table == "event_deliveries":
            rows = list(self.db.deliveries.values())
            rows = [row for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
            if self.update_values:
                for row in rows:
                    row.update(self.update_values)
            return Result(rows)
        return Result()


class Database:
    def __init__(self, events):
        self.events = events
        self.deliveries = {}
        self.updates = []

    def table(self, name):
        return Query(self, name)


def test_poll_pages_past_first_fifty_events():
    db = Database([
        {"id": f"e-{i}", "event_type": "task.ready", "payload": {"i": i}, "source_instance": "remote"}
        for i in range(51)
    ])
    bus = SharedEventBus(db, source_service="director")
    seen = []
    bus.subscribe("task.ready", lambda payload: seen.append(payload["i"]))

    assert bus.poll() == 51
    assert seen == list(range(51))


def test_failed_delivery_is_not_acknowledged_and_can_be_retried():
    db = Database([])
    bus = SharedEventBus(db, source_service="director")

    bus._mark_delivery_processed("delivery-1", success=False, error="temporary failure")

    update = db.updates[-1][1]
    assert update["processed"] is False
    assert update["processed_at"] is None
    assert update["processing_error"] == "temporary failure"
    assert "claimed_at" not in update


def test_successful_delivery_is_acknowledged_and_releases_lease():
    db = Database([])
    bus = SharedEventBus(db, source_service="director")

    bus._mark_delivery_processed("delivery-1", success=True)

    update = db.updates[-1][1]
    assert update["processed"] is True
    assert update["processed_at"]
    assert update["claimed_by"] is None
    assert update["claimed_at"] is None
