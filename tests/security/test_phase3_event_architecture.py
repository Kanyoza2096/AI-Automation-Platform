from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from core.shared_event_bus import SharedEventBus
from core.unified_bus import UnifiedEventBus
from utils.ids import get_process_instance_id


class _Result:
    def __init__(self, data=None):
        self.data = data if data is not None else []


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.operation = "select"
        self.values = None
        self.filters = []
        self.in_filters = []
        self.range_value = None

    def insert(self, value):
        self.operation = "insert"
        self.values = dict(value)
        return self

    def update(self, value):
        self.operation = "update"
        self.values = dict(value)
        return self

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def in_(self, key, values):
        self.in_filters.append((key, set(values)))
        return self

    def is_(self, *_args):
        return self

    def lte(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def range(self, start, end):
        self.range_value = (start, end)
        return self

    def or_(self, *_args):
        return self

    def execute(self):
        if self.table == "events":
            rows = list(self.db.events)
            for key, values in self.in_filters:
                rows = [row for row in rows if row.get(key) in values]
            if self.range_value is not None:
                start, end = self.range_value
                rows = rows[start:end + 1]
            return _Result(rows)

        rows = self.db.tables.setdefault(self.table, [])
        if self.operation == "insert":
            if self.table == "event_deliveries":
                unique = ("event_id", "consumer_group")
            elif self.table == "event_handler_deliveries":
                unique = ("event_id", "consumer_group", "handler_key")
            else:
                unique = ()
            if unique and any(
                all(row.get(key) == self.values.get(key) for key in unique)
                for row in rows
            ):
                raise ValueError("duplicate receipt")
            record = dict(self.values)
            record.setdefault("id", f"{self.table}-{uuid.uuid4().hex[:8]}")
            record.setdefault("processed", False)
            record.setdefault("claimed_by", None)
            record.setdefault("claimed_at", None)
            record.setdefault("attempt_count", 0)
            record.setdefault("dead_lettered_at", None)
            rows.append(record)
            return _Result([dict(record)])

        matched = [
            row for row in rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        if self.operation == "update":
            for row in matched:
                row.update(self.values)
        return _Result([dict(row) for row in matched])


class _DB:
    def __init__(self):
        self.events = []
        self.tables = {
            "event_deliveries": [],
            "event_handler_deliveries": [],
        }

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        assert name == "complete_shared_event_delivery"
        db = self

        class _RPC:
            def execute(self):
                row = next(
                    (
                        item for item in db.tables["event_deliveries"]
                        if item.get("id") == params["p_delivery_id"]
                        and item.get("consumer_group") == params["p_consumer_group"]
                        and item.get("claimed_by") == params["p_claim_token"]
                    ),
                    None,
                )
                if row is None:
                    return _Result(False)
                success = bool(params["p_success"])
                row.update({
                    "processed": success,
                    "processed_at": datetime.now(timezone.utc).isoformat() if success else None,
                    "processing_error": None if success else params.get("p_error"),
                    "attempt_count": row.get("attempt_count", 0) + (0 if success else 1),
                    "claimed_by": None,
                    "claimed_at": None,
                })
                return _Result(True)

        return _RPC()


def test_shared_bus_uses_per_consumer_and_per_handler_delivery_receipts():
    db = _DB()
    db.events = [{
        "id": "event-1",
        "event_type": "job.created",
        "payload": {"workspace_id": "ws-1"},
        "source_instance": "remote:abc",
    }]
    bus = SharedEventBus(db, source_service="director")
    seen = []
    bus.subscribe("job.created", lambda payload: seen.append(payload))

    assert bus.poll() == 1
    assert seen == [{"workspace_id": "ws-1"}]
    group_receipt = db.tables["event_deliveries"][0]
    handler_receipt = db.tables["event_handler_deliveries"][0]
    assert group_receipt["event_id"] == "event-1"
    assert group_receipt["consumer_group"] == "director"
    assert group_receipt["processed"] is True
    assert handler_receipt["event_id"] == "event-1"
    assert handler_receipt["processed"] is True


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
    assert db.tables["event_deliveries"] == []


def test_unified_bus_persistence_uses_events_schema_and_tenant_metadata():
    class _PersistQuery:
        def __init__(self, db):
            self.db = db

        def upsert(self, records, on_conflict=None):
            assert on_conflict == "id"
            self.db.records = records
            return self

        def execute(self):
            return _Result([{}])

    class _PersistDB:
        def __init__(self):
            self.records = None

        def table(self, name):
            assert name == "events"
            return _PersistQuery(self)

    async def run():
        db = _PersistDB()
        bus = UnifiedEventBus(db)
        event_id = await bus.publish(
            "publish.requested",
            {"x": 1},
            source="director",
            workspace_id="ws-1",
            correlation_id="corr-1",
            decision_id="dec-1",
            parent_event_id="parent-evt-1",
            schema_version="2.0",
        )
        await bus.stop()
        return event_id, db.records

    event_id, records = asyncio.run(run())
    assert records[0]["id"] == event_id
    assert records[0]["event_type"] == "publish.requested"
    assert records[0]["workspace_id"] == "ws-1"
    assert records[0]["correlation_id"] == "corr-1"
    assert records[0]["source_service"] == "director"
    assert records[0]["source_instance"] == get_process_instance_id()
    assert records[0]["parent_event_id"] == "parent-evt-1"
    assert records[0]["schema_version"] == "2.0"
    assert records[0]["payload"]["_nerve"]["event_id"] == event_id
    assert records[0]["payload"]["_nerve"]["event_name"] == "publish.requested"
    assert records[0]["payload"]["_nerve"]["parent_event_id"] == "parent-evt-1"
    assert records[0]["payload"]["_nerve"]["schema_version"] == "2.0"


def test_nerve_normalization_tolerates_malformed_legacy_metadata():
    from core.nerve import validate_and_normalize, workspace_from_payload

    for malformed in (None, "legacy", ["not", "a", "mapping"]):
        envelope = validate_and_normalize(
            "legacy.event", {"_nerve": malformed}, strict=False
        )
        assert envelope.workspace_id == "unbound"
        assert envelope.correlation_id
        assert workspace_from_payload({"_nerve": malformed}) is None
