from __future__ import annotations

from dataclasses import dataclass

from core.shared_event_bus import SharedEventBus


@dataclass
class Result:
    data: list


class StatefulQuery:
    """Small Supabase query fake for exercising the complete poll lifecycle."""

    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.operation = "select"
        self.values = None
        self.filters = []
        self.or_filter = None

    def select(self, *_args):
        return self

    def insert(self, values):
        self.operation = "insert"
        self.values = dict(values)
        return self

    def update(self, values):
        self.operation = "update"
        self.values = dict(values)
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def or_(self, expression):
        self.or_filter = expression
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table_name, [])

        if self.operation == "insert":
            if self.table_name == "event_deliveries":
                key = (self.values["event_id"], self.values["consumer_group"])
                if any((r["event_id"], r["consumer_group"]) == key for r in rows):
                    raise ValueError("duplicate delivery")
                record = dict(self.values)
                record["id"] = f"delivery-{len(rows) + 1}"
                record.setdefault("claimed_by", None)
                record.setdefault("claimed_at", None)
                record.setdefault("processed", False)
                rows.append(record)
                return Result([record])
            rows.append(dict(self.values))
            return Result([self.values])

        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters)
        ]

        if self.operation == "update":
            # The fake models the null-lease branch of the atomic claim query.
            if self.or_filter and "claimed_at.is.null" in self.or_filter:
                matched = [r for r in matched if r.get("claimed_at") is None]
            for row in matched:
                row.update(self.values)
            return Result([dict(r) for r in matched])

        return Result([dict(r) for r in matched])


class StatefulDB:
    def __init__(self, events):
        self.tables = {"events": [dict(event) for event in events], "event_deliveries": []}

    def table(self, name):
        return StatefulQuery(self, name)


def test_poll_completes_shared_delivery_lifecycle_end_to_end():
    event = {
        "id": "event-1",
        "event_type": "engineering.task.completed",
        "payload": {
            "task_id": "task-7",
            "_nerve": {
                "event_id": "event-1",
                "event_name": "engineering.task.completed",
                "workspace_id": "workspace-7",
                "correlation_id": "corr-7",
                "source": "engineering-api",
                "schema_version": "1.0",
            },
        },
        "source_instance": "another-process",
        "processed": False,
    }
    db = StatefulDB([event])
    bus = SharedEventBus(db, source_service="engineering-worker")
    received = []
    bus.subscribe("engineering.task.completed", lambda payload: received.append(payload))

    assert bus.poll() == 1
    assert len(received) == 1
    assert received[0]["task_id"] == "task-7"
    assert received[0]["_nerve"]["correlation_id"] == "corr-7"

    deliveries = db.tables["event_deliveries"]
    assert len(deliveries) == 1
    assert deliveries[0]["event_id"] == "event-1"
    assert deliveries[0]["consumer_group"] == "engineering-worker"
    assert deliveries[0]["processed"] is True
    assert deliveries[0]["claimed_by"] is None
    assert deliveries[0]["claimed_at"] is None

    # A second poll must not redeliver a successfully acknowledged event.
    assert bus.poll() == 0
    assert len(received) == 1



def test_failed_delivery_is_retried_after_lease_expiry():
    event = {
        "id": "event-retry-1",
        "event_type": "engineering.task.completed",
        "payload": {"task_id": "task-retry-1"},
        "source_instance": "another-process",
        "processed": False,
    }
    db = StatefulDB([event])
    bus = SharedEventBus(db, source_service="engineering-worker")
    attempts = []

    def flaky_handler(payload):
        attempts.append(payload["task_id"])
        if len(attempts) == 1:
            raise RuntimeError("temporary downstream failure")

    bus.subscribe("engineering.task.completed", flaky_handler)

    # First attempt fails: it must remain pending and retain its lease.
    assert bus.poll() == 1
    delivery = db.tables["event_deliveries"][0]
    assert delivery["processed"] is False
    assert delivery["processing_error"] == "one or more handlers failed"
    assert delivery["claimed_by"] is not None
    assert delivery["claimed_at"] is not None

    # Simulate lease expiry. The next poll can now claim the same delivery.
    delivery["claimed_at"] = None
    assert bus.poll() == 1

    assert attempts == ["task-retry-1", "task-retry-1"]
    assert delivery["processed"] is True
    assert delivery["claimed_by"] is None
    assert delivery["claimed_at"] is None



def test_async_handler_finishes_before_delivery_is_acknowledged():
    event = {
        "id": "event-async-1",
        "event_type": "engineering.task.completed",
        "payload": {"task_id": "task-async-1"},
        "source_instance": "another-process",
        "processed": False,
    }
    db = StatefulDB([event])
    bus = SharedEventBus(db, source_service="engineering-worker")
    observed = []

    async def async_handler(payload):
        observed.append(payload["task_id"])

    bus.subscribe("engineering.task.completed", async_handler)
    assert bus.poll() == 1
    assert observed == ["task-async-1"]
    delivery = db.tables["event_deliveries"][0]
    assert delivery["processed"] is True
    assert delivery["claimed_by"] is None
