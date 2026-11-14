import asyncio
import json
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from services.director.models import Authority, Decision, Risk, Sector
from services.director.website_event_adapter import DirectorWebsiteEventAdapter


class FakeTable:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.filters = {}
        self.mode = "select"

    def select(self, *_args):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _count):
        return self

    def contains(self, key, value):
        self.contains_filter = (key, value)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def insert(self, row):
        self.row = row
        self.mode = "insert"
        return self

    def upsert(self, row, **_kwargs):
        self.row = row
        self.mode = "upsert"
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.name, [])
        if self.mode == "select":
            selected = [
                row for row in rows
                if all(str(row.get(key)) == str(value) for key, value in self.filters.items())
            ]
            if hasattr(self, "contains_filter"):
                key, expected = self.contains_filter
                def includes(actual, wanted):
                    if not isinstance(actual, dict) or not isinstance(wanted, dict):
                        return actual == wanted
                    return all(k in actual and includes(actual[k], v) for k, v in wanted.items())
                selected = [row for row in selected if includes(row.get(key), expected)]
            return SimpleNamespace(data=selected)
        if self.mode == "insert":
            if any(row.get("id") == self.row.get("id") for row in rows if self.row.get("id")):
                raise RuntimeError("duplicate key")
            rows.append(dict(self.row))
            return SimpleNamespace(data=[self.row])
        if self.mode == "upsert":
            rows[:] = [row for row in rows if row.get("id") != self.row.get("id")]
            rows.append(dict(self.row))
            return SimpleNamespace(data=[self.row])
        raise AssertionError(self.mode)


class FakeRPC:
    def __init__(self, db, name, params):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        if self.name != "record_director_website_event_observation":
            raise AssertionError(self.name)
        p = self.params
        with self.db.rpc_lock:
            existing = [
                row for row in self.db.rows["director_observations"]
                if row.get("workspace_id") == p["p_workspace_id"]
                and row.get("payload", {}).get("source") == "website_integration_events"
                and row.get("payload", {}).get("metrics", {}).get("integration_id") == p["p_integration_id"]
                and row.get("payload", {}).get("metrics", {}).get("website_event_id") == p["p_event_id"]
            ]
            if existing:
                return SimpleNamespace(data=[{"observation_id": existing[0]["id"], "inserted": False}])
            row = {
                "id": len(self.db.rows["director_observations"]) + 1,
                "workspace_id": p["p_workspace_id"],
                "timestamp": p["p_timestamp"],
                "payload": p["p_payload"],
            }
            self.db.rows["director_observations"].append(row)
            return SimpleNamespace(data=[{"observation_id": row["id"], "inserted": True}])


class FakeDB:
    def __init__(self):
        self.rpc_lock = threading.Lock()
        self.rows = {
            "website_integrations": [{
                "workspace_id": "ws-1",
                "integration_id": "site-demo",
                "status": "configured",
                "event_types": ["blog.created"],
            }],
            "website_integration_events": [{
                "workspace_id": "ws-1",
                "integration_id": "site-demo",
                "event_id": "evt-growthbridge-0001",
                "event_type": "blog.created",
                "occurred_at": "2026-11-14T10:00:00+00:00",
                "payload": {"resource_id": "post-1"},
            }],
            "director_decisions": [],
            "director_observations": [],
        }

    def table(self, name):
        return FakeTable(self, name)

    def rpc(self, name, params):
        return FakeRPC(self, name, params)


class FakeBus:
    def __init__(self):
        self.handlers = {}

    async def subscribe(self, event_name, handler):
        self.handlers.setdefault(event_name, []).append(handler)

        def unsubscribe():
            self.handlers[event_name].remove(handler)

        return unsubscribe

    async def emit(self, event_name, context):
        for handler in self.handlers.get(event_name, []):
            await handler(context)


class FakeDirector:
    def __init__(self):
        self.observers = {}
        self.decisions = deque()
        self.tasks = []

    def register_observer(self, observer):
        self.observers[observer.name] = observer


def event_context(source="nextjs-website-integration"):
    return {
        "event_name": "website.integration.blog.created",
        "source": source,
        "event_id": "internal-event-uuid",
        "workspace_id": "ws-1",
        "brand_id": "brand-demo",
        "payload": {
            "integration_id": "site-demo",
            "event_id": "evt-growthbridge-0001",
            "payload": {"resource_id": "post-1"},
        },
    }


def build_adapter():
    director = FakeDirector()
    bus = FakeBus()
    db = FakeDB()
    adapter = DirectorWebsiteEventAdapter(director, bus, db)
    adapter.register_observer()
    return adapter, director, bus, db


def test_valid_website_event_persists_observation_and_decision_without_task():
    async def scenario():
        adapter, director, bus, db = build_adapter()
        await adapter.start()
        result = await bus.emit(
            "website.integration.blog.created", event_context()
        )
        assert result is None  # Event bus handlers acknowledge by completion.
        assert len(db.rows["director_observations"]) == 1
        assert len(db.rows["director_decisions"]) == 1
        decision = db.rows["director_decisions"][0]["payload"]
        assert decision["outcome"] == "observed"
        assert decision["workspace_id"] == "ws-1"
        assert decision["action_taken"]["task_created"] is False
        assert decision["action_taken"]["public_publishing_authorized"] is False
        assert director.tasks == []
        observation = director.observers[adapter.OBSERVER_NAME].observe()
        assert observation.status.value == "unknown"
        assert observation.metrics["event_count"] == 1
        assert observation.metrics["event_type_counts"] == {"blog.created": 1}
        assert "events" not in observation.metrics
        assert "workspace_id" not in observation.metrics
        assert observation.metrics["director_signals"] == []
        await adapter.stop()
        assert all(not handlers for handlers in bus.handlers.values())

    asyncio.run(scenario())


def test_duplicate_growthbridge_event_is_idempotent():
    async def scenario():
        adapter, _director, bus, db = build_adapter()
        await adapter.start()
        await bus.emit("website.integration.blog.created", event_context())
        await bus.emit(
            "website.integration.blog.created",
            {**event_context(), "event_id": "another-internal-event-uuid"},
        )
        assert len(db.rows["director_observations"]) == 1
        assert adapter.observe().metrics["event_count"] == 1
        assert len(db.rows["director_decisions"]) == 1
        await adapter.stop()

    asyncio.run(scenario())


def test_untrusted_source_is_rejected_before_database_write():
    async def scenario():
        adapter, _director, _bus, db = build_adapter()
        with pytest.raises(ValueError, match="untrusted_website_event_source"):
            await adapter.handle_event(event_context(source="unknown"))
        assert db.rows["director_observations"] == []
        assert db.rows["director_decisions"] == []

    asyncio.run(scenario())


def test_event_without_durable_workspace_receipt_is_rejected():
    async def scenario():
        adapter, _director, _bus, db = build_adapter()
        db.rows["website_integration_events"].clear()
        with pytest.raises(ValueError, match="website_event_receipt_not_verified"):
            await adapter.handle_event(event_context())
        assert db.rows["director_observations"] == []
        assert db.rows["director_decisions"] == []

    asyncio.run(scenario())


def test_inactive_integration_cannot_feed_director():
    async def scenario():
        adapter, _director, _bus, db = build_adapter()
        db.rows["website_integrations"][0]["status"] = "revoked"
        with pytest.raises(ValueError, match="website_event_receipt_not_verified"):
            await adapter.handle_event(event_context())
        assert db.rows["director_observations"] == []
        assert db.rows["director_decisions"] == []

    asyncio.run(scenario())

def test_duplicate_replay_after_restart_hydrates_observer_without_duplicate_rows():
    async def scenario():
        _first_adapter, _director, _bus, db = build_adapter()
        # Simulate a previously completed delivery stored durably.
        decision_id = "website_event_" + __import__("hashlib").sha256(
            b"ws-1:site-demo:evt-growthbridge-0001"
        ).hexdigest()[:24]
        db.rows["director_decisions"].append({
            "id": decision_id,
            "payload": {"outcome": "observed"},
        })
        db.rows["director_observations"].append({
            "id": 1,
            "workspace_id": "ws-1",
            "payload": {
                "metrics": {"website_event_id": "evt-growthbridge-0001"}
            },
        })
        adapter, _director2, bus2, _db2 = build_adapter()
        adapter.supabase = db
        await adapter.start()
        await bus2.emit("website.integration.blog.created", event_context())
        assert len(db.rows["director_observations"]) == 1
        assert len(db.rows["director_decisions"]) == 1
        assert adapter.observe().metrics["event_count"] == 1
        await adapter.stop()

    asyncio.run(scenario())


def test_dead_letter_replay_recovers_workspace_only_from_unique_durable_receipt():
    async def scenario():
        adapter, _director, bus, db = build_adapter()
        replay = event_context(source="dead_letter_replay")
        replay.pop("workspace_id")
        await adapter.start()
        await bus.emit("website.integration.blog.created", replay)
        assert len(db.rows["director_observations"]) == 1
        assert len(db.rows["director_decisions"]) == 1
        decision = db.rows["director_decisions"][0]["payload"]
        assert decision["workspace_id"] == "ws-1"
        await adapter.stop()

    asyncio.run(scenario())


def test_dead_letter_replay_rejects_ambiguous_workspace_receipts():
    async def scenario():
        adapter, _director, _bus, db = build_adapter()
        duplicate_receipt = dict(db.rows["website_integration_events"][0])
        duplicate_receipt["workspace_id"] = "ws-2"
        db.rows["website_integration_events"].append(duplicate_receipt)
        replay = event_context(source="dead_letter_replay")
        replay.pop("workspace_id")
        with pytest.raises(ValueError, match="website_event_receipt_not_verified"):
            await adapter.handle_event(replay)
        assert db.rows["director_observations"] == []
        assert db.rows["director_decisions"] == []

    asyncio.run(scenario())



def test_start_hydrates_persisted_observations_after_process_restart():
    async def scenario():
        first_adapter, _director, _bus, db = build_adapter()
        await first_adapter.handle_event(event_context())
        second_adapter, _director2, bus2, _db2 = build_adapter()
        second_adapter.supabase = db
        await second_adapter.start()
        observation = second_adapter.observe()
        assert observation.metrics["event_count"] == 1
        assert observation.metrics["event_type_counts"] == {"blog.created": 1}
        assert "workspace_id" not in observation.metrics
        await second_adapter.stop()

    asyncio.run(scenario())


def test_process_wide_observer_never_exposes_cross_workspace_identifiers():
    adapter, _director, _bus, _db = build_adapter()
    adapter._remember_event(
        "ws-2", "site-other", "evt-other", "blog.published",
        "2026-11-14T11:00:00+00:00", "brand-other",
    )
    observation = adapter.observe()
    assert observation.metrics["event_count"] == 2
    assert observation.metrics["event_type_counts"] == {
        "blog.created": 1,
        "blog.published": 1,
    }
    rendered_metrics = repr(observation.metrics)
    for tenant_value in (
        "ws-1", "ws-2", "site-demo", "site-other",
        "evt-growthbridge-0001", "evt-other", "brand-demo", "brand-other",
    ):
        assert tenant_value not in rendered_metrics



def test_atomic_observation_rpc_deduplicates_concurrent_appends():
    async def scenario():
        adapter, _director, _bus, db = build_adapter()
        observation = adapter._build_test_observation() if hasattr(adapter, "_build_test_observation") else None
        if observation is None:
            from services.director.models import Health, Observation, Sector
            from datetime import datetime, timezone
            observation = Observation(
                source=adapter.OBSERVER_NAME,
                sector=Sector.BRANDS,
                status=Health.UNKNOWN,
                metrics={
                    "workspace_id": "ws-1",
                    "integration_id": "site-demo",
                    "website_event_id": "evt-growthbridge-0001",
                    "event_type": "blog.created",
                    "occurred_at": "2026-11-14T10:00:00+00:00",
                    "brand_id": "brand-demo",
                },
                issues=[],
                message="test",
                timestamp=datetime.now(timezone.utc),
            )
        results = await asyncio.gather(*[
            asyncio.to_thread(
                adapter._persist_observation,
                observation,
                "ws-1",
                "site-demo",
                "evt-growthbridge-0001",
            )
            for _ in range(12)
        ])
        assert sum(results) == 1
        assert len(db.rows["director_observations"]) == 1

    asyncio.run(scenario())



def test_concurrent_decision_claim_conflict_is_idempotent_only_for_same_workspace():
    from datetime import datetime, timezone

    adapter, _director, _bus, db = build_adapter()
    decision = Decision(
        id="website_event_test-claim",
        timestamp=datetime.now(timezone.utc),
        decision="Observe",
        reason="Test deterministic claim race",
        confidence=0.0,
        sector=Sector.BRANDS,
        authority=Authority.NOTIFY,
        risk=Risk.LOW,
        outcome="observation_pending",
        stage="observation",
    )
    # Simulate another worker committing the same deterministic claim first.
    db.rows["director_decisions"].append({
        "id": decision.id,
        "workspace_id": "ws-1",
        "payload": {"workspace_id": "ws-1", "outcome": "observation_pending"},
    })
    adapter._insert_decision_claim(decision, "ws-1")
    assert len(db.rows["director_decisions"]) == 1

    with pytest.raises(RuntimeError, match="duplicate key"):
        adapter._insert_decision_claim(decision, "ws-2")
    assert len(db.rows["director_decisions"]) == 1
