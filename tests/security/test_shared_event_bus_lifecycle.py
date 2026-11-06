from __future__ import annotations

from dataclasses import dataclass

from core.shared_event_bus import SharedEventBus


@dataclass
class Result:
    data: list


class StatefulQuery:
    """Small Supabase fake for group and per-handler receipt lifecycle tests."""

    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.operation = "select"
        self.values = None
        self.filters = []
        self.or_filter = None
        self.limit_value = None

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

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table_name, [])

        if self.operation == "insert":
            if self.table_name in ("event_deliveries", "event_handler_deliveries"):
                keys = (
                    ("event_id", "consumer_group")
                    if self.table_name == "event_deliveries"
                    else ("event_id", "consumer_group", "handler_key")
                )
                key = tuple(self.values[k] for k in keys)
                if any(tuple(row.get(k) for k in keys) == key for row in rows):
                    raise ValueError("duplicate receipt")
                record = dict(self.values)
                record["id"] = f"{self.table_name}-{len(rows) + 1}"
                record.setdefault("claimed_by", None)
                record.setdefault("claimed_at", None)
                record.setdefault("processed", False)
                rows.append(record)
                return Result([dict(record)])
            rows.append(dict(self.values))
            return Result([dict(self.values)])

        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        if self.or_filter and "claimed_at.is.null" in self.or_filter:
            matched = [row for row in matched if row.get("claimed_at") is None]

        if self.operation == "update":
            for row in matched:
                row.update(self.values)
            return Result([dict(row) for row in matched])

        if self.limit_value is not None:
            matched = matched[: self.limit_value]
        return Result([dict(row) for row in matched])


class StatefulDB:
    def __init__(self, events):
        self.tables = {
            "events": [dict(event) for event in events],
            "event_deliveries": [],
            "event_handler_deliveries": [],
        }

    def table(self, name):
        return StatefulQuery(self, name)


def _event(event_id="event-1"):
    return {
        "id": event_id,
        "event_type": "engineering.task.completed",
        "payload": {
            "task_id": "task-7",
            "_nerve": {
                "event_id": event_id,
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


def test_poll_completes_group_and_handler_receipts_end_to_end():
    db = StatefulDB([_event()])
    bus = SharedEventBus(db, source_service="engineering-worker")
    received = []
    bus.subscribe("engineering.task.completed", lambda payload: received.append(payload))

    assert bus.poll() == 1
    assert len(received) == 1
    assert received[0]["task_id"] == "task-7"
    assert received[0]["_nerve"]["correlation_id"] == "corr-7"

    group_receipt = db.tables["event_deliveries"][0]
    assert group_receipt["processed"] is True
    assert group_receipt["claimed_by"] is None
    handler_receipt = db.tables["event_handler_deliveries"][0]
    assert handler_receipt["event_id"] == "event-1"
    assert handler_receipt["consumer_group"] == "engineering-worker"
    assert handler_receipt["processed"] is True
    assert handler_receipt["claimed_by"] is None

    assert bus.poll() == 0
    assert len(received) == 1


def test_failed_handler_is_retried_after_lease_expiry():
    db = StatefulDB([_event("event-retry-1")])
    bus = SharedEventBus(db, source_service="engineering-worker")
    attempts = []

    def flaky_handler(payload):
        attempts.append(payload["task_id"])
        if len(attempts) == 1:
            raise RuntimeError("temporary downstream failure")

    bus.subscribe("engineering.task.completed", flaky_handler)
    assert bus.poll() == 1
    group_receipt = db.tables["event_deliveries"][0]
    handler_receipt = db.tables["event_handler_deliveries"][0]
    assert group_receipt["processed"] is False
    assert handler_receipt["processed"] is False
    assert handler_receipt["processing_error"] == "temporary downstream failure"

    group_receipt["claimed_at"] = None
    handler_receipt["claimed_at"] = None
    assert bus.poll() == 1
    assert attempts == ["task-7", "task-7"]
    assert group_receipt["processed"] is True
    assert handler_receipt["processed"] is True


def test_async_handler_finishes_before_both_receipts_are_acknowledged():
    db = StatefulDB([_event("event-async-1")])
    bus = SharedEventBus(db, source_service="engineering-worker")
    observed = []

    async def async_handler(payload):
        observed.append(payload["task_id"])

    bus.subscribe("engineering.task.completed", async_handler)
    assert bus.poll() == 1
    assert observed == ["task-7"]
    assert db.tables["event_deliveries"][0]["processed"] is True
    assert db.tables["event_handler_deliveries"][0]["processed"] is True


def test_partial_handler_failure_skips_already_completed_handler_on_retry():
    db = StatefulDB([_event("event-partial-1")])
    bus = SharedEventBus(db, source_service="engineering-worker")
    first_handler_effects = []
    second_handler_attempts = []

    def first_handler(payload):
        first_handler_effects.append(payload["_nerve"]["event_id"])

    def second_handler(payload):
        second_handler_attempts.append(payload["_nerve"]["event_id"])
        if len(second_handler_attempts) == 1:
            raise RuntimeError("temporary failure after first handler completed")

    bus.subscribe("engineering.task.completed", first_handler)
    bus.subscribe("engineering.task.completed", second_handler)

    assert bus.poll() == 1
    group_receipt = db.tables["event_deliveries"][0]
    handler_receipts = db.tables["event_handler_deliveries"]
    assert len(handler_receipts) == 2
    assert sum(bool(receipt["processed"]) for receipt in handler_receipts) == 1

    # Simulate lease expiry. The successful handler receipt remains complete.
    group_receipt["claimed_at"] = None
    for receipt in handler_receipts:
        if not receipt["processed"]:
            receipt["claimed_at"] = None

    assert bus.poll() == 1
    assert first_handler_effects == ["event-partial-1"]
    assert second_handler_attempts == ["event-partial-1", "event-partial-1"]
    assert all(receipt["processed"] is True for receipt in handler_receipts)
    assert group_receipt["processed"] is True
