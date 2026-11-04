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
        self.rpc_name = None
        self.rpc_params = None

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

    def is_(self, *args): return self
    def lte(self, *args): return self
    def not_(self): return self
    def rpc(self, name, params):
        self.rpc_name = name
        self.rpc_params = params
        return self

    def execute(self):
        if self.table == "events":
            rows = self.db.events
            if self.window:
                start, end = self.window
                rows = rows[start:end + 1]
            return Result(rows)
        if self.rpc_name:
            self.db.rpc_calls.append((self.rpc_name, self.rpc_params))
            return Result(True)
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
        self.rpc_calls = []

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


def test_failed_delivery_is_recorded_for_retry_and_eventual_dead_letter():
    db = Database([])
    bus = SharedEventBus(db, source_service="director")

    bus._mark_delivery_processed(
        "delivery-1",
        claim_token="worker:claim-1",
        success=False,
        error="temporary failure",
    )

    name, params = db.rpc_calls[-1]
    assert name == "complete_shared_event_delivery"
    assert params["p_delivery_id"] == "delivery-1"
    assert params["p_claim_token"] == "worker:claim-1"
    assert params["p_success"] is False
    assert params["p_error"] == "one or more handlers failed"
    assert params["p_max_attempts"] == 5


def test_successful_delivery_is_acknowledged_with_claim_fencing():
    db = Database([])
    bus = SharedEventBus(db, source_service="director")

    bus._mark_delivery_processed(
        "delivery-1", claim_token="worker:claim-2", success=True
    )

    name, params = db.rpc_calls[-1]
    assert name == "complete_shared_event_delivery"
    assert params["p_success"] is True
    assert params["p_claim_token"] == "worker:claim-2"
