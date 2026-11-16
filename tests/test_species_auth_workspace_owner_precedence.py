from types import SimpleNamespace

from core.species_auth import SpeciesAuth


class FakeQuery:
    def __init__(self, tables, table_name):
        self.tables = tables
        self.table_name = table_name
        self.filters = {}
        self.limit_value = None

    def select(self, _columns):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = self.tables.get(self.table_name, [])
        rows = [
            row for row in rows
            if all(str(row.get(key)) == str(value) for key, value in self.filters.items())
        ]
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return SimpleNamespace(data=rows)


class FakeSupabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, table_name):
        return FakeQuery(self.tables, table_name)


def test_workspace_owner_is_not_downgraded_by_viewer_membership():
    client = FakeSupabase({
        "workspace_members": [{
            "workspace_id": "ws-owner",
            "role": "viewer",
            "accepted_at": "2026-11-01T00:00:00Z",
        }],
        "workspaces": [{
            "id": "ws-owner",
            "owner_email": "Owner@Example.com",
        }],
    })

    assert SpeciesAuth()._resolve_workspace_for_email(
        client, "owner@example.com", "auth-user-1"
    ) == ("ws-owner", "owner")


def test_non_owner_keeps_authoritative_membership_role():
    client = FakeSupabase({
        "workspace_members": [{
            "workspace_id": "ws-member",
            "role": "viewer",
            "accepted_at": "2026-11-01T00:00:00Z",
        }],
        "workspaces": [{
            "id": "ws-member",
            "owner_email": "different-owner@example.com",
        }],
    })

    assert SpeciesAuth()._resolve_workspace_for_email(
        client, "member@example.com", "auth-user-2"
    ) == ("ws-member", "viewer")


def test_explicitly_requested_owned_workspace_overrides_stale_membership():
    client = FakeSupabase({
        "workspace_members": [{
            "workspace_id": "ws-selected",
            "role": "viewer",
            "accepted_at": "2026-11-01T00:00:00Z",
        }],
        "workspaces": [{
            "id": "ws-selected",
            "owner_email": "owner@example.com",
        }],
    })

    assert SpeciesAuth()._resolve_workspace_for_email(
        client, "owner@example.com", "auth-user-3", "ws-selected"
    ) == ("ws-selected", "owner")
