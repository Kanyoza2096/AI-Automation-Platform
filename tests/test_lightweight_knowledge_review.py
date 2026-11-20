from scripts import lightweight_knowledge_review as reviewer


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.workspace_id = None
        self.start = 0
        self.end = 99

    def select(self, *_args):
        return self

    def eq(self, key, value):
        if key == "workspace_id":
            self.workspace_id = value
        return self

    def order(self, *_args):
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        rows = self.rows
        if self.workspace_id is not None:
            rows = [row for row in rows if row["workspace_id"] == self.workspace_id]
        return FakeResult(rows[self.start:self.end + 1])


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def table(self, _name):
        self.query = FakeQuery(self.rows)
        return self.query


def test_review_is_read_only_and_scoped_to_authenticated_workspace(monkeypatch):
    client = FakeClient([
        {"id": "a", "workspace_id": "workspace-a", "brand_id": None, "content": "A", "metadata": {}},
        {"id": "b", "workspace_id": "workspace-b", "brand_id": None, "content": "B", "metadata": {}},
    ])
    monkeypatch.setattr(reviewer, "_client", lambda: client)

    result = reviewer.run(workspace_id="workspace-a")

    assert result["scanned"] == 1
    assert result["workspace_scoped"] is True
    assert result["mode"] == "dry_run"
    assert result["verified_entries_created"] == 0
    assert result["promotions_performed"] == 0
    assert client.query.workspace_id == "workspace-a"


def test_review_rejects_empty_explicit_workspace_scope():
    import pytest

    with pytest.raises(ValueError):
        reviewer.run(workspace_id=" ")
