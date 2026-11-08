"""Workspace validation for durable organism census hydration."""

from types import SimpleNamespace

from core.species_scale import OrganismStateStore


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters = {}

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        if self.table_name == "organism_state":
            return SimpleNamespace(data=self.client.organism_rows)
        if self.table_name == "workspaces":
            if self.client.fail_validation:
                raise RuntimeError("workspace registry unavailable")
            workspace_id = self.filters.get("id")
            return SimpleNamespace(data=[
                row for row in self.client.workspace_rows
                if row["id"] == workspace_id
            ])
        raise AssertionError(f"Unexpected table: {self.table_name}")


class _Client:
    def __init__(self, organism_rows, workspace_rows, fail_validation=False):
        self.organism_rows = organism_rows
        self.workspace_rows = workspace_rows
        self.fail_validation = fail_validation

    def table(self, name):
        return _Query(self, name)


class _Registry:
    def __init__(self):
        self.created = []

    def get_or_create(self, workspace_id, **_kwargs):
        self.created.append(workspace_id)
        return SimpleNamespace(stats={})


def _row(workspace_id):
    return {
        "workspace_id": workspace_id,
        "plan": "free",
        "industry_organs": [],
        "timezone": "UTC",
        "director_authority": "suggest",
        "stats": {},
    }


def test_hydration_skips_synthetic_and_unknown_workspaces_without_deleting_rows():
    client = _Client(
        organism_rows=[_row("default"), _row("ws-orphan"), _row("kdo-engineering-staging")],
        workspace_rows=[{"id": "kdo-engineering-staging", "status": "active"}],
    )
    store = OrganismStateStore(client)
    registry = _Registry()

    hydrated = store.hydrate_registry(registry)

    assert hydrated == 1
    assert registry.created == ["kdo-engineering-staging"]
    assert len(client.organism_rows) == 3  # forensic rows remain untouched


def test_hydration_skips_inactive_workspace():
    client = _Client(
        organism_rows=[_row("ws-suspended")],
        workspace_rows=[{"id": "ws-suspended", "status": "suspended"}],
    )
    registry = _Registry()

    assert OrganismStateStore(client).hydrate_registry(registry) == 0
    assert registry.created == []


def test_hydration_fails_closed_when_workspace_validation_is_unavailable():
    client = _Client(
        organism_rows=[_row("kdo-engineering-staging")],
        workspace_rows=[{"id": "kdo-engineering-staging", "status": "active"}],
        fail_validation=True,
    )
    registry = _Registry()

    assert OrganismStateStore(client).hydrate_registry(registry) == 0
    assert registry.created == []
