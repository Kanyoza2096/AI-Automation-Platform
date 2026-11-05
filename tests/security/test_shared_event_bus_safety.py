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
    monkeypatch.setattr(
        bus,
        "_mark_delivery_processed",
        lambda delivery_id, **kwargs: observed.append((delivery_id, kwargs)) or True,
    )
    bus._dispatch_event(
        {"id": "event-1", "event_type": "task.failed", "payload": {"task_id": "t1"}},
        delivery_id="delivery-1",
        claim_token="worker-a:claim-1",
    )
    assert observed == [(
        "delivery-1",
        {
            "claim_token": "worker-a:claim-1",
            "success": False,
            "error": "one or more handlers failed",
        },
    )]
