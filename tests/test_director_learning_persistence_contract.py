"""Durable Director learning contract: persist, restart, and replay without missions.

The fake Supabase transport models acknowledged PostgREST writes and reads while
retaining rows across Director instances. This exercises AsyncSupabaseStore and
Director's real learning/replay methods without touching a live database.
"""
from types import SimpleNamespace

import pytest

from services.director import Director
from services.director.models import Authority, Risk, Sector, Status, Task


class DurableFakeSupabase:
    def __init__(self):
        self.rows = {"director_learnings": {}}

    def table(self, name):
        return FakeQuery(self.rows.setdefault(name, {}))


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.operation = "select"
        self.payload = None
        self.filters = []
        self.sort_desc = False
        self.row_limit = None
        self.row_range = None
        self.ids = None

    def upsert(self, payload, **kwargs):
        self.operation = "upsert"
        self.payload = dict(payload)
        return self

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def gte(self, key, value):
        self.filters.append(("gte", key, value))
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def order(self, _key, desc=False):
        self.sort_desc = desc
        return self

    def limit(self, value):
        self.row_limit = value
        return self

    def range(self, start, end):
        self.row_range = (start, end)
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def in_(self, _key, values):
        self.ids = set(values)
        return self

    def execute(self):
        if self.operation == "upsert":
            self.rows[self.payload["id"]] = self.payload
            return SimpleNamespace(data=[self.payload])
        if self.operation == "delete":
            for row_id in self.ids or ():
                self.rows.pop(row_id, None)
            return SimpleNamespace(data=[])
        values = list(self.rows.values())
        for kind, key, expected in self.filters:
            if kind == "gte":
                values = [row for row in values if str(row.get(key, "")) >= expected]
            elif kind == "eq":
                values = [row for row in values if row.get(key) == expected]
        values.sort(key=lambda row: str(row.get("timestamp", "")), reverse=self.sort_desc)
        if self.row_range is not None:
            start, end = self.row_range
            values = values[start:end + 1]
        elif self.row_limit is not None:
            values = values[:self.row_limit]
        return SimpleNamespace(data=[dict(row) for row in values])


@pytest.mark.asyncio
async def test_verified_learning_survives_director_restart_and_replays_from_store():
    database = DurableFakeSupabase()
    first = Director(supabase_client=database)
    task = Task(
        id="staging-durable-learning-contract",
        sector=Sector.SELF,
        description="Isolated Director persistence contract test",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.VERIFIED,
        result={"ok": True},
    )

    persisted_and_applied = await first._record_verified_learning(task)

    assert persisted_and_applied is True
    assert task.id in first._learning_recorded_task_ids
    assert len(first.learning_loop.signals) == 1
    assert len(database.rows["director_learnings"]) == 1

    # A fresh Director has no in-memory learning state; it must rebuild solely
    # from the durable row written by the first Director instance.
    restarted = Director(supabase_client=database)
    await restarted._replay_learning_history()

    assert task.id in restarted._learning_recorded_task_ids
    assert restarted.learning_loop.verification_stats["self_diagnostic"]["verified"] == 1
    assert restarted.learning_loop.capability_stats["self_diagnostic"]["successes"] == 1
    assert len(restarted.learning_loop._replayed_signal_ids) == 1



@pytest.mark.asyncio
async def test_authoritative_ineffective_learning_survives_restart_and_replays():
    database = DurableFakeSupabase()
    first = Director(supabase_client=database)
    task = Task(
        id="staging-durable-ineffective-contract",
        sector=Sector.SELF,
        description="Isolated Director authoritative negative-outcome test",
        capability="self_diagnostic",
        authority=Authority.SUGGEST,
        risk=Risk.LOW,
        status=Status.INEFFECTIVE,
        result={
            "verified": False,
            "outcome_verification": {
                "outcome": "ineffective",
                "reason": "authoritative_state_mismatch",
            },
        },
    )

    persisted_and_applied = await first._record_verified_learning(task)

    assert persisted_and_applied is True
    assert task.id in first._learning_recorded_task_ids
    assert len(first.learning_loop.signals) == 1
    assert first.learning_loop.signals[0].outcome == "failure"
    assert len(database.rows["director_learnings"]) == 1

    restarted = Director(supabase_client=database)
    await restarted._replay_learning_history()

    assert task.id in restarted._learning_recorded_task_ids
    assert restarted.learning_loop.verification_stats["self_diagnostic"]["ineffective"] == 1
    assert restarted.learning_loop.capability_stats["self_diagnostic"]["failures"] == 1
    assert restarted.learning_loop.capability_stats["self_diagnostic"]["successes"] == 0
    assert len(restarted.learning_loop._replayed_signal_ids) == 1


@pytest.mark.asyncio
async def test_store_rejects_mismatched_verified_outcome_contract():
    database = DurableFakeSupabase()
    director = Director(supabase_client=database)
    mismatched = {
        "id": "mismatched-learning-contract",
        "task_id": "task-mismatch",
        "outcome": "failure",
        "verification_status": "verified",
        "capability": "self_diagnostic",
        "sector": Sector.SELF.value,
        "context": {"status": Status.VERIFIED.value},
    }

    assert await director.store.save_learning(mismatched) is False
    assert database.rows["director_learnings"] == {}
