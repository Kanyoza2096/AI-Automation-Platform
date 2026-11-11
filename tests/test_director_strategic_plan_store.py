import asyncio
import json
from types import SimpleNamespace

from services.director.stores import AsyncSupabaseStore


class FakeQuery:
    def __init__(self, client):
        self.client = client
        self.operation = None
        self.filters = {}

    def upsert(self, row):
        self.operation = "upsert"
        self.row = row
        return self

    def select(self, *_columns):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _limit):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operation == "upsert":
            self.client.rows[self.row["plan_id"]] = dict(self.row)
            return SimpleNamespace(data=[dict(self.row)])
        plan_id = self.filters.get("plan_id")
        if plan_id is not None:
            row = self.client.rows.get(plan_id)
            rows = [{"payload": row["payload"]}] if row else []
        else:
            rows = [{"payload": row["payload"]} for row in self.client.rows.values()]
        if self.client.read_payload_as_legacy_string:
            rows = [
                {"payload": json.dumps(row["payload"]) if isinstance(row["payload"], dict) else row["payload"]}
                for row in rows
            ]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self):
        self.rows = {}
        self.read_payload_as_legacy_string = False

    def table(self, _name):
        return FakeQuery(self)


def sample_plan():
    return {
        "schema_version": "kdo.director.strategic-plan.v1",
        "plan_id": "dplan_test_jsonb_object",
        "objective": {"objective_id": "objective-test"},
        "summary": "A persisted test plan.",
        "steps": [
            {"step_id": "step-1", "capability": "ask_with_tools"},
            {"step_id": "step-2", "capability": "run_health_scan"},
        ],
        "limitations": ["Planning only."],
        "source_context_fingerprint": "fingerprint-test",
    }


def test_strategic_plan_write_uses_json_object_and_verifies_readback():
    client = FakeSupabase()
    store = AsyncSupabaseStore(client)
    plan = sample_plan()

    saved = asyncio.run(store.save_strategic_plan(plan))

    assert saved is True
    stored_payload = client.rows[plan["plan_id"]]["payload"]
    assert isinstance(stored_payload, dict)
    assert stored_payload["steps"] == plan["steps"]
    assert stored_payload["summary"] == plan["summary"]
    assert asyncio.run(store.get_strategic_plan(plan["plan_id"])) == plan
    assert asyncio.run(store.list_strategic_plans()) == [plan]


def test_strategic_plan_reader_keeps_legacy_json_string_compatibility():
    client = FakeSupabase()
    plan = sample_plan()
    client.rows[plan["plan_id"]] = {"payload": json.dumps(plan)}
    store = AsyncSupabaseStore(client)

    assert asyncio.run(store.get_strategic_plan(plan["plan_id"])) == plan
    assert asyncio.run(store.list_strategic_plans()) == [plan]


def test_strategic_plan_write_rejects_legacy_json_string_scalar_readback():
    client = FakeSupabase()
    client.read_payload_as_legacy_string = True
    store = AsyncSupabaseStore(client)

    saved = asyncio.run(store.save_strategic_plan(sample_plan()))

    assert saved is False
    # The normal read API remains backward-compatible with legacy rows.
    plan_id = "dplan_test_jsonb_object"
    assert asyncio.run(store.get_strategic_plan(plan_id)) == sample_plan()
