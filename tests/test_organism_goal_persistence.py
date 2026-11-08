from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from organism.goals import GoalPriority, GoalProposal
from organism.goal_persistence import GoalProposalStore, GoalProposalStoreError


NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters = {}
        self.operation = "select"
        self.record = None
        self.limit_value = None

    def select(self, _columns):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gt(self, key, value):
        self.filters[f"{key}__gt"] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def upsert(self, record, on_conflict=None, ignore_duplicates=False):
        assert on_conflict == "proposal_id"
        assert ignore_duplicates is True
        self.operation = "upsert"
        self.record = record
        return self

    def execute(self):
        if self.client.fail:
            raise ConnectionError("database unavailable")
        if self.table_name == "workspaces":
            rows = [r for r in self.client.workspaces if r["id"] == self.filters.get("id")]
            return SimpleNamespace(data=rows)
        if self.operation == "upsert":
            key = self.record["proposal_id"]
            if key not in self.client.proposals:
                self.client.proposals[key] = self.record
            return SimpleNamespace(data=[self.client.proposals[key]])
        rows = [
            row for row in self.client.proposals.values()
            if row["workspace_id"] == self.filters.get("workspace_id")
            and row["status"] == self.filters.get("status")
            and row["expires_at"] > self.filters.get("expires_at__gt", "")
        ]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return SimpleNamespace(data=rows[:self.limit_value])


class Client:
    def __init__(self):
        self.proposals = {}
        self.workspaces = [{"id": "workspace-a", "status": "active"}]
        self.fail = False

    def table(self, name):
        return Query(self, name)


def proposal(**changes):
    values = dict(
        proposal_id="gprop_123",
        workspace_id="workspace-a",
        title="Restore api-worker",
        success_condition="api-worker reports healthy",
        source_event_ids=("evt-123",),
        priority=GoalPriority.HIGH,
        risk_level="medium",
        expected_outcome="api-worker reports healthy",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        authority="suggest",
        status="proposed",
    )
    values.update(changes)
    return GoalProposal(**values)


@pytest.mark.asyncio
async def test_persist_and_retrieve_pending_proposal_round_trip():
    client = Client()
    store = GoalProposalStore(client)

    assert await store.persist(proposal()) == 1
    restored = await store.list_pending("workspace-a", now=NOW)

    assert len(restored) == 1
    assert restored[0].proposal_id == "gprop_123"
    assert restored[0].source_event_ids == ("evt-123",)
    assert restored[0].authority == "suggest"
    assert restored[0].status == "proposed"


@pytest.mark.asyncio
async def test_persist_is_idempotent_and_does_not_overwrite_existing_row():
    client = Client()
    store = GoalProposalStore(client)
    await store.persist(proposal())
    client.proposals["gprop_123"]["title"] = "Existing reviewed title"

    await store.persist(proposal(title="Replay with changed title"))

    assert client.proposals["gprop_123"]["title"] == "Existing reviewed title"


@pytest.mark.asyncio
async def test_pending_query_is_workspace_scoped_and_excludes_expired_proposals():
    client = Client()
    store = GoalProposalStore(client)
    await store.persist(proposal())
    await store.persist(proposal(
        proposal_id="gprop_expired",
        created_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    ))

    rows = await store.list_pending("workspace-a", now=NOW)
    assert [item.proposal_id for item in rows] == ["gprop_123"]
    assert await store.list_pending("workspace-b", now=NOW) == ()


@pytest.mark.asyncio
async def test_unknown_or_inactive_workspace_cannot_read_or_write_proposals():
    client = Client()
    store = GoalProposalStore(client)
    with pytest.raises(GoalProposalStoreError, match="unknown_workspace"):
        await store.persist(proposal(workspace_id="workspace-missing"))
    client.workspaces[0]["status"] = "suspended"
    with pytest.raises(GoalProposalStoreError, match="inactive_workspace"):
        await store.list_pending("workspace-a", now=NOW)


@pytest.mark.asyncio
async def test_database_failure_is_not_reported_as_empty_proposals():
    client = Client()
    client.fail = True
    store = GoalProposalStore(client)
    with pytest.raises(GoalProposalStoreError, match="persistence failed"):
        await store.persist(proposal())


def test_goal_proposal_rejects_execution_authority_or_non_proposed_status():
    with pytest.raises(ValueError, match="suggest-only"):
        proposal(authority="execute")
    with pytest.raises(ValueError, match="start as proposed"):
        proposal(status="approved")
