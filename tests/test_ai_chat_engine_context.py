import asyncio

from api.routers.ai_chat import _load_engine_context, _safe_context_value


class FakeQuery:
    def __init__(self, rows, calls, table):
        self.rows = rows
        self.calls = calls
        self.table = table
        self.filters = {}

    def select(self, columns):
        self.calls.append((self.table, "select", columns))
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, column, desc=False):
        self.calls.append((self.table, "order", column, desc))
        return self

    def limit(self, count):
        self.calls.append((self.table, "limit", count))
        return self

    def execute(self):
        self.calls.append((self.table, "filters", dict(self.filters)))
        return type("Result", (), {"data": self.rows})()


class FakeDB:
    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.calls = []

    def table(self, name):
        return FakeQuery(self.rows_by_table.get(name, []), self.calls, name)


def test_context_redacts_credentials_and_bounds_nested_data():
    cleaned = _safe_context_value({
        "access_token": "do-not-include",
        "safe": "x" * 1500,
        "nested": {"client_secret": "hidden", "summary": "visible"},
    })
    assert "access_token" not in cleaned
    assert len(cleaned["safe"]) == 1000
    assert "client_secret" not in cleaned["nested"]
    assert cleaned["nested"]["summary"] == "visible"


def test_director_context_queries_only_the_authenticated_workspace():
    db = FakeDB({
        "director_tasks": [{
            "id": "task-1",
            "status": "approval_required",
            "created_at": "2026-11-16T12:00:00Z",
            "payload": {"objective": "Review reliability", "api_key": "must-not-leak"},
        }],
        "director_decisions": [{
            "id": "decision-1",
            "timestamp": "2026-11-16T12:00:00Z",
            "payload": {"summary": "Investigate gateway latency"},
        }],
    })
    result = asyncio.run(_load_engine_context(db, "ws-verified", "director"))
    filters = [call[2] for call in db.calls if len(call) == 3 and call[1] == "filters"]
    assert filters and all(item == {"workspace_id": "ws-verified"} for item in filters)
    assert result["sources"]["scope"] == "authenticated_workspace"
    assert result["evidence"]["workspace_tasks"][0]["payload"]["objective"] == "Review reliability"
    assert "api_key" not in result["evidence"]["workspace_tasks"][0]["payload"]


def test_guardian_adapter_labels_aggregate_coverage_not_raw_findings():
    db = FakeDB({
        "daily_summaries": [{
            "date": "2026-11-16",
            "guardian_issues_found": 3,
            "guardian_critical": 1,
            "created_at": "2026-11-16T12:00:00Z",
        }],
    })
    result = asyncio.run(_load_engine_context(db, "ws-verified", "guardian"))
    assert result["evidence"]["daily_security_summary"][0]["guardian_critical"] == 1
    assert "not raw scan findings" in result["sources"]["coverage"]
    assert db.calls[-1][2] == {"workspace_id": "ws-verified"}
