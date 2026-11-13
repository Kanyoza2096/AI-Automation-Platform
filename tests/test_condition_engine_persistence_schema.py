from datetime import datetime, timezone
from types import SimpleNamespace

from services.condition_engine.core import AutonomousConditionEngine


class FakeQuery:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table

    def upsert(self, payload, on_conflict=None):
        self.client.calls.append((self.table_name, payload, on_conflict))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)


def _engine_with_fake_supabase():
    engine = object.__new__(AutonomousConditionEngine)
    engine.supabase = FakeSupabase()
    engine.config = SimpleNamespace(persist_decisions=True)
    return engine


def test_decision_persistence_uses_existing_jsonb_contract_and_generated_key():
    engine = _engine_with_fake_supabase()
    decision = SimpleNamespace(
        id="decision-123",
        brand_id="brand-1",
        state=SimpleNamespace(value="proposed"),
        confidence=0.91,
        reason="evidence-backed",
        recommended_action="observe",
        risk=SimpleNamespace(value="low"),
        requires_approval=False,
        evidence={"workspace_id": "ws-1", "source": "test"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    engine._persist_decision(decision)

    table, payload, conflict = engine.supabase.calls[0]
    assert table == "autonomous_decisions"
    assert conflict == "decision_key"
    assert "id" not in payload
    assert payload["workspace_id"] == "ws-1"
    assert payload["decision"]["id"] == "decision-123"
    assert payload["decision"]["confidence"] == 0.91


def test_decision_memory_persistence_uses_existing_jsonb_contract_and_generated_key():
    engine = _engine_with_fake_supabase()
    memory = SimpleNamespace(
        decision_id="decision-123",
        brand_id="brand-1",
        action="observe",
        reason="completed",
        confidence=0.91,
        expected_value=0.8,
        actual_value=0.9,
        success=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        evaluated_at=None,
        workspace_id="ws-1",
    )

    engine._persist_decision_memory(memory)

    table, payload, conflict = engine.supabase.calls[0]
    assert table == "autonomous_decision_memory"
    assert conflict == "memory_key"
    assert payload["key"] == "observe"
    assert payload["value"]["decision_id"] == "decision-123"
    assert payload["value"]["brand_id"] == "brand-1"
