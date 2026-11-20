from scripts import lightweight_knowledge_review as review


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.operation = "select"
        self.payload = None
        self.filters = {}

    def select(self, *_args):
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args):
        return self

    def range(self, start, end):
        self.range_value = (start, end)
        return self

    def execute(self):
        if self.operation == "select":
            start, end = self.range_value
            return FakeResult(self.client.rows[start:end + 1])
        row = next((r for r in self.client.rows
                    if all(r.get(k) == v for k, v in self.filters.items())), None)
        if row is None:
            return FakeResult([])
        row["metadata"] = self.payload["metadata"]
        self.client.saved += 1
        return FakeResult([{"id": row["id"]}])


class FakeClient:
    def __init__(self):
        self.rows = [
            {"id": "a", "workspace_id": "w1", "brand_id": "b1",
             "content": "First passage.", "metadata": {"keep": "yes"}},
            {"id": "b", "workspace_id": "w1", "brand_id": "b1",
             "content": "Second passage.", "metadata": {}},
            {"id": "c", "workspace_id": "w2", "brand_id": "b2",
             "content": "Other workspace.", "metadata": {}},
        ]
        self.saved = 0

    def table(self, name):
        assert name == "knowledge_entries"
        return FakeQuery(self, name)


def test_apply_persists_only_check_reports_with_workspace_scope(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(review, "_client", lambda: client)

    result = review.run(apply=True, batch_size=2, workspace_id="w1")

    assert result["mode"] == "apply"
    assert result["workspace_scoped"] is True
    assert result["scanned"] == 2
    assert result["write_attempted"] == 2
    assert result["write_confirmed"] == 2
    assert result["write_failed"] == 0
    assert client.saved == 2
    for row in client.rows[:2]:
        assert row["metadata"]["kdo_lightweight_review"]["verified"] is False
        assert row["metadata"]["kdo_lightweight_review"]["approval_granted"] is False
        assert row["metadata"]["kdo_lightweight_review"]["publishing_evidence_authorized"] is False
    assert client.rows[0]["metadata"]["keep"] == "yes"
    assert "kdo_lightweight_review" not in client.rows[2]["metadata"]
