from __future__ import annotations

from core.shared_event_bus import SharedEventBus


class Result:
    def __init__(self, data=None):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.calls = []
        self.values = None

    def update(self, values):
        self.values = dict(values)
        self.calls.append(("update", self.values))
        return self

    def select(self, *args):
        self.calls.append(("select", args))
        return self

    def eq(self, key, value):
        self.calls.append(("eq", key, value))
        return self

    def is_(self, key, value):
        self.calls.append(("is", key, value))
        return self

    def lte(self, key, value):
        self.calls.append(("lte", key, value))
        return self

    def or_(self, value):
        self.calls.append(("or", value))
        return self

    @property
    def not_(self):
        self.calls.append(("not",))
        return self

    def order(self, *args, **kwargs):
        self.calls.append(("order", args, kwargs))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        self.db.last_query = self
        if self.table == "rpc":
            return Result(self.db.rpc_result)
        return Result(self.db.rows)


class Database:
    def __init__(self, rpc_result=True, rows=None):
        self.rpc_result = rpc_result
        self.rows = rows or []
        self.last_query = None
        self.rpc_calls = []

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        self.rpc_calls.append((name, dict(params)))
        return Query(self, "rpc")


def test_delivery_claim_obeys_retry_schedule_and_dead_letter_state():
    db = Database()
    bus = SharedEventBus(db, source_service="director")

    claim = bus._claim_delivery("event-1")

    assert claim is None
    query = db.last_query
    assert ("eq", "event_id", "event-1") in query.calls
    assert ("eq", "consumer_group", "director") in query.calls
    assert ("eq", "processed", False) in query.calls
    assert ("is", "dead_lettered_at", "null") in query.calls
    assert any(call[0] == "lte" and call[1] == "next_attempt_at" for call in query.calls)
    assert query.values["claimed_by"].startswith(bus.instance_id + ":")


def test_failed_delivery_uses_fenced_retry_rpc():
    db = Database(rpc_result=True)
    bus = SharedEventBus(db, source_service="director")

    assert bus._mark_delivery_processed(
        "delivery-1", claim_token="worker:claim-1",
        success=False, error="temporary failure",
    )
    name, params = db.rpc_calls[-1]
    assert name == "complete_shared_event_delivery"
    assert params == {
        "p_delivery_id": "delivery-1",
        "p_consumer_group": "director",
        "p_claim_token": "worker:claim-1",
        "p_success": False,
        "p_error": "temporary failure",
        "p_max_attempts": 5,
    }


def test_stale_delivery_claim_cannot_be_acknowledged():
    db = Database(rpc_result=False)
    bus = SharedEventBus(db, source_service="director")

    assert bus._mark_delivery_processed(
        "delivery-1", claim_token="worker:stale-claim", success=True
    ) is False


def test_dead_letter_inspection_is_scoped_to_consumer_group():
    rows = [{"id": "d1", "consumer_group": "director", "dead_lettered_at": "2026-11-01"}]
    db = Database(rows=rows)
    bus = SharedEventBus(db, source_service="director")

    assert bus.get_dead_letters(limit=500) == rows
    calls = db.last_query.calls
    assert ("eq", "consumer_group", "director") in calls
    assert ("not",) in calls
    assert ("limit", 200) in calls


def test_dead_letter_requeue_resets_retry_state_and_is_group_scoped():
    db = Database(rows=[{"id": "d1"}])
    bus = SharedEventBus(db, source_service="director")

    assert bus.requeue_dead_letter("d1") is True
    query = db.last_query
    assert query.values["attempt_count"] == 0
    assert query.values["dead_lettered_at"] is None
    assert query.values["processing_error"] is None
    assert query.values["claimed_by"] is None
    assert query.values["claimed_at"] is None
    assert ("eq", "id", "d1") in query.calls
    assert ("eq", "consumer_group", "director") in query.calls
    assert ("not",) in query.calls
