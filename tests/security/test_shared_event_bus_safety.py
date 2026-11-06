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


def test_shared_bus_failed_handler_delivery_remains_retryable_and_fenced():
    class Result:
        data = [{"id": "delivery-1"}]

    class Query:
        def __init__(self, db):
            self.db = db
        def update(self, values):
            self.db.update_values = values
            return self
        def eq(self, key, value):
            self.db.filters.append((key, value))
            return self
        def select(self, *args):
            return self
        def execute(self):
            return Result()

    class DB:
        def __init__(self):
            self.filters = []
            self.update_values = None
        def table(self, name):
            assert name == "event_deliveries"
            return Query(self)

    db = DB()
    bus = SharedEventBus(db, source_service="worker-a")
    token = "worker-a:claim-123"
    acknowledged = bus._mark_delivery_processed(
        "delivery-1",
        claim_token=token,
        success=False,
        error="handler failed",
    )
    assert acknowledged is True
    assert db.update_values["processed"] is False
    assert db.update_values["processed_at"] is None
    assert db.update_values["processing_error"] == "handler failed"
    assert ("claimed_by", token) in db.filters
    assert ("processed", False) in db.filters
    # Failed work retains claimed_by/claimed_at until lease expiry, preventing
    # immediate hot-loop retries while still allowing recovery after timeout.
    assert "claimed_by" not in db.update_values
    assert "claimed_at" not in db.update_values


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
        def order(self, *args, **kwargs): return self
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
