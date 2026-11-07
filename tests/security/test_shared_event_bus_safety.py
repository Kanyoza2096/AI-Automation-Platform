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



def test_shared_bus_claim_returns_delivery_id_and_fencing_token():
    class Result:
        data = [{"id": "delivery-1"}]

    class Query:
        def update(self, values):
            self.values = values
            return self
        def eq(self, *args):
            return self
        def or_(self, *args):
            return self
        def select(self, *args):
            return self
        def execute(self):
            return Result()

    class DB:
        def table(self, name):
            assert name == "event_deliveries"
            return Query()

    bus = SharedEventBus(DB(), source_service="worker-a")
    delivery = bus._claim_delivery("event-1")
    assert delivery is not None
    delivery_id, claim_token = delivery
    assert delivery_id == "delivery-1"
    assert claim_token.startswith(bus.instance_id + ":")


def test_shared_bus_failed_delivery_uses_fenced_retry_rpc():
    class Result:
        data = True

    class RPC:
        def __init__(self, db, name, params):
            self.db = db
            self.name = name
            self.params = params
        def execute(self):
            self.db.calls.append((self.name, self.params))
            return Result()

    class DB:
        def __init__(self):
            self.calls = []
        def rpc(self, name, params):
            return RPC(self, name, params)

    db = DB()
    bus = SharedEventBus(db, source_service="worker-a")
    token = "worker-a:claim-123"
    acknowledged = bus._mark_delivery_processed(
        "delivery-1", claim_token=token, success=False, error="handler failed"
    )
    assert acknowledged is True
    name, params = db.calls[0]
    assert name == "complete_shared_event_delivery"
    assert params["p_delivery_id"] == "delivery-1"
    assert params["p_consumer_group"] == "worker-a"
    assert params["p_claim_token"] == token
    assert params["p_success"] is False
    assert params["p_error"] == "handler failed"
    assert params["p_max_attempts"] == 5


def test_failed_handler_receipt_releases_handler_lease_for_scheduled_group_retry():
    class Result:
        data = [{"id": "receipt-1"}]

    class Query:
        def __init__(self):
            self.filters = []
            self.values = None
        def update(self, values):
            self.values = values
            return self
        def eq(self, key, value):
            self.filters.append((key, value))
            return self
        def select(self, *args):
            return self
        def execute(self):
            return Result()

    class DB:
        def __init__(self):
            self.query = Query()
        def table(self, name):
            assert name == "event_handler_deliveries"
            return self.query

    db = DB()
    bus = SharedEventBus(db, source_service="worker-a")
    assert bus._mark_handler_delivery_processed(
        "receipt-1", claim_token="worker-a:handler-claim",
        success=False, error="temporary failure",
    )
    assert db.query.values["processed"] is False
    assert db.query.values["claimed_by"] is None
    assert db.query.values["claimed_at"] is None
    assert ("claimed_by", "worker-a:handler-claim") in db.query.filters


def test_shared_bus_ack_without_claim_token_fails_closed():
    bus = SharedEventBus(source_service="worker-a")
    assert bus._mark_delivery_processed("delivery-1", claim_token=None) is False



def test_shared_bus_dispatch_passes_claim_token_and_keeps_failed_work_retryable(monkeypatch):
    bus = SharedEventBus(source_service="worker-a")
    observed = []
    bus.subscribe("task.failed", lambda payload: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(bus, "_ensure_handler_delivery", lambda event_id, handler_key: {"id": "handler-receipt-1", "processed": False})
    monkeypatch.setattr(bus, "_claim_handler_delivery", lambda receipt_id: (receipt_id, "handler-claim-1"))
    monkeypatch.setattr(
        bus,
        "_mark_handler_delivery_processed",
        lambda receipt_id, **kwargs: observed.append(("handler", receipt_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        bus,
        "_mark_delivery_processed",
        lambda delivery_id, **kwargs: observed.append(("group", delivery_id, kwargs)) or True,
    )
    bus._dispatch_event(
        {"id": "event-1", "event_type": "task.failed", "payload": {"task_id": "t1"}},
        delivery_id="delivery-1",
        claim_token="worker-a:claim-1",
    )
    assert observed[0] == (
        "handler", "handler-receipt-1",
        {"claim_token": "handler-claim-1", "success": False, "error": "boom"},
    )
    assert observed[1] == (
        "group", "delivery-1",
        {
            "claim_token": "worker-a:claim-1",
            "success": False,
            "error": "one or more handlers failed or remain leased",
        },
    )



def test_shared_bus_persists_canonical_nerve_metadata():
    class Result:
        data = [{"id": "event-1"}]

    class Query:
        def __init__(self, db):
            self.db = db
        def insert(self, record):
            self.db.record = record
            return self
        def execute(self):
            return Result()

    class DB:
        record = None
        def table(self, name):
            assert name == "events"
            return Query(self)

    db = DB()
    bus = SharedEventBus(db, source_service="engineering-worker")
    assert bus.publish(
        "engineering.task.completed",
        {"task_id": "task-7"},
        workspace_id="workspace-2",
        correlation_id="corr-7",
        decision_id="decision-3",
        brand_id="brand-4",
        parent_event_id="event-parent-1",
    ) is True

    record = db.record
    assert record["event_type"] == "engineering.task.completed"
    assert record["workspace_id"] == "workspace-2"
    assert record["correlation_id"] == "corr-7"
    assert record["decision_id"] == "decision-3"
    assert record["brand_id"] == "brand-4"
    assert record["parent_event_id"] == "event-parent-1"
    assert record["source_service"] == "engineering-worker"
    assert record["source_instance"] == bus.process_instance_id
    assert record["schema_version"] == "1.0"
    assert record["payload"]["task_id"] == "task-7"
    assert record["payload"]["_nerve"]["event_id"] == record["id"]
    assert record["payload"]["_nerve"]["event_name"] == record["event_type"]
    assert record["payload"]["_nerve"]["workspace_id"] == "workspace-2"
    assert record["payload"]["_nerve"]["correlation_id"] == "corr-7"
    assert record["payload"]["_nerve"]["parent_event_id"] == "event-parent-1"
    assert record["payload"]["_nerve"]["schema_version"] == "1.0"



def test_shared_bus_skips_events_persisted_by_unified_bus_in_same_process():
    class Query:
        def __init__(self, rows):
            self.rows = rows
        def select(self, *args): return self
        def in_(self, column, values):
            assert column == "event_type"
            self.rows = [row for row in self.rows if row.get(column) in values]
            return self
        def order(self, *args, **kwargs): return self
        def range(self, start, end):
            self.rows = self.rows[start:end + 1]
            return self
        def limit(self, *args): return self
        def execute(self):
            return type("Result", (), {"data": self.rows})()

    class DB:
        def __init__(self, rows):
            self.rows = rows
        def table(self, name):
            assert name == "events"
            return Query(self.rows)

    bus = SharedEventBus(source_service="worker-a")
    db = DB([{
        "id": "event-from-local-unified-bus",
        "event_type": "task.created",
        "payload": {"task_id": "t1"},
        "source_instance": bus.process_instance_id,
    }])
    bus.supabase = db
    received = []
    bus.subscribe("task.created", lambda payload: received.append(payload))
    assert bus.poll() == 0
    assert received == []



def test_handler_subscription_identity_is_stable_and_repeated_wiring_is_idempotent():
    bus = SharedEventBus(source_service="worker-a")

    def on_event(payload):
        return payload

    first = bus.subscribe("task.created", on_event)
    second = bus.subscribe("task.created", on_event)
    assert len(bus._handlers["task.created"]) == 1
    first()
    assert bus._handlers["task.created"] == []


def test_handler_receipt_ack_is_fenced_by_claim_token():
    class Result:
        data = []

    class Query:
        def __init__(self):
            self.filters = []
            self.values = None

        def update(self, values):
            self.values = values
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def select(self, *_args):
            return self

        def execute(self):
            return Result()

    class DB:
        def __init__(self):
            self.query = Query()

        def table(self, name):
            assert name == "event_handler_deliveries"
            return self.query

    db = DB()
    bus = SharedEventBus(db, source_service="worker-a")
    acknowledged = bus._mark_handler_delivery_processed(
        "handler-receipt-1",
        claim_token="replacement-worker:claim-2",
        success=True,
    )
    assert acknowledged is False
    assert ("claimed_by", "replacement-worker:claim-2") in db.query.filters
    assert ("processed", False) in db.query.filters
    assert db.query.values["processed"] is True



def test_handler_lease_renewal_is_fenced_by_current_claim_token():
    class Result:
        data = [{"id": "handler-receipt-1"}]

    class Query:
        def __init__(self):
            self.filters = []
            self.values = None

        def update(self, values):
            self.values = values
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def select(self, *_args):
            return self

        def execute(self):
            return Result()

    class DB:
        def __init__(self):
            self.query = Query()

        def table(self, name):
            assert name == "event_handler_deliveries"
            return self.query

    db = DB()
    bus = SharedEventBus(db, source_service="worker-a")
    assert bus._renew_handler_delivery_lease(
        "handler-receipt-1", "worker-a:current-claim"
    ) is True
    assert ("id", "handler-receipt-1") in db.query.filters
    assert ("claimed_by", "worker-a:current-claim") in db.query.filters
    assert ("processed", False) in db.query.filters
    assert "claimed_at" in db.query.values


def test_handler_lease_renewal_rejects_a_stale_worker():
    class Result:
        data = []

    class Query:
        def __init__(self):
            self.filters = []

        def update(self, _values):
            return self

        def eq(self, key, value):
            self.filters.append((key, value))
            return self

        def select(self, *_args):
            return self

        def execute(self):
            return Result()

    class DB:
        def __init__(self):
            self.query = Query()

        def table(self, name):
            assert name == "event_handler_deliveries"
            return self.query

    db = DB()
    bus = SharedEventBus(db, source_service="worker-a")
    assert bus._renew_handler_delivery_lease(
        "handler-receipt-1", "stale-worker:old-claim"
    ) is False
    assert ("claimed_by", "stale-worker:old-claim") in db.query.filters


def test_poll_filters_unrelated_event_types_before_page_limit(monkeypatch):
    class Query:
        def __init__(self, rows):
            self.rows = rows
            self.event_types = None
            self.offset = 0
            self.limit = 0
        def select(self, *_args): return self
        def in_(self, column, values):
            assert column == "event_type"
            self.event_types = set(values)
            return self
        def order(self, *_args, **_kwargs): return self
        def range(self, start, end):
            self.offset, self.limit = start, end - start + 1
            return self
        def execute(self):
            rows = [row for row in self.rows if row["event_type"] in self.event_types]
            return type("Result", (), {"data": rows[self.offset:self.offset + self.limit]})()

    class DB:
        def __init__(self, rows): self.query = Query(rows)
        def table(self, name):
            assert name == "events"
            return self.query

    rows = [
        {"id": f"old-{i}", "event_type": "unrelated.heartbeat", "source_instance": "other"}
        for i in range(100)
    ]
    rows.append({"id": "target", "event_type": "target.event", "source_instance": "other"})
    db = DB(rows)
    bus = SharedEventBus(db, source_service="worker")
    bus.subscribe("target.event", lambda _payload: None)
    dispatched = []
    monkeypatch.setattr(bus, "_ensure_delivery", lambda *_args: True)
    monkeypatch.setattr(bus, "_claim_delivery", lambda event_id: (f"d-{event_id}", "claim"))
    monkeypatch.setattr(bus, "_dispatch_event", lambda event, **_kwargs: dispatched.append(event["id"]))

    assert bus.poll() == 1
    assert dispatched == ["target"]
    assert db.query.event_types == {"target.event"}
    assert db.query.limit == bus.MAX_EVENTS_PER_POLL


def test_poll_advances_pages_when_matching_backlog_exceeds_page_size(monkeypatch):
    class Query:
        def __init__(self, rows, db):
            self.rows, self.db = rows, db
            self.offset, self.limit, self.event_types = 0, 0, set()
        def select(self, *_args): return self
        def in_(self, column, values):
            assert column == "event_type"
            self.event_types = set(values)
            return self
        def order(self, *_args, **_kwargs): return self
        def range(self, start, end):
            self.offset, self.limit = start, end - start + 1
            self.db.offsets.append(start)
            return self
        def execute(self):
            rows = [row for row in self.rows if row["event_type"] in self.event_types]
            return type("Result", (), {"data": rows[self.offset:self.offset + self.limit]})()

    class DB:
        def __init__(self, rows): self.rows, self.offsets = rows, []
        def table(self, name):
            assert name == "events"
            return Query(self.rows, self)

    rows = [{"id": f"e-{i}", "event_type": "target.event", "source_instance": "other"} for i in range(52)]
    db = DB(rows)
    bus = SharedEventBus(db, source_service="worker")
    bus.subscribe("target.event", lambda _payload: None)
    seen = set()
    dispatched = []
    monkeypatch.setattr(bus, "_ensure_delivery", lambda *_args: True)
    def claim(event_id):
        if event_id in seen:
            return None
        seen.add(event_id)
        return (f"d-{event_id}", "claim")
    monkeypatch.setattr(bus, "_claim_delivery", claim)
    monkeypatch.setattr(bus, "_dispatch_event", lambda event, **_kwargs: dispatched.append(event["id"]))

    assert bus.poll() == 50
    assert bus.poll() == 2
    assert len(dispatched) == 52
    assert db.offsets == [0, 50]
    # Once the final short page is reached, the next cycle restarts at zero.
    assert bus.poll() == 0
    assert db.offsets == [0, 50, 0]
