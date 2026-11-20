import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers import ai_chat

class FakeCollaborationQuery:
    def __init__(self, db, operation="select", values=None):
        self.db = db
        self.operation = operation
        self.values = values or {}

    def select(self, *_args):
        self.operation = "select"
        return self

    def update(self, values):
        self.operation = "update"
        self.values = values
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        if self.operation == "select":
            return SimpleNamespace(data=[{"evidence_summary": self.db.evidence_summary}])
        self.db.consultations = self.values.get("consultations", self.db.consultations)
        self.db.evidence_summary = self.values.get("evidence_summary", self.db.evidence_summary)
        return SimpleNamespace(data=[])


class FakeCollaborationDB:
    def __init__(self):
        self.evidence_summary = {}
        self.consultations = []

    def table(self, _name):
        return FakeCollaborationQuery(self)



def test_collaboration_engine_allowlist_is_closed_and_deduplicated():
    assert ai_chat._normalize_collaboration_engines(["guardian", "ai_brain", "guardian"]) == [
        "guardian", "ai_brain"
    ]
    with pytest.raises(HTTPException):
        ai_chat._normalize_collaboration_engines(["director"])
    with pytest.raises(HTTPException):
        ai_chat._normalize_collaboration_engines(["general"])
    with pytest.raises(HTTPException):
        ai_chat._normalize_collaboration_engines([])


def test_director_collaboration_runs_read_only_consults_then_synthesis(monkeypatch):
    calls = []

    async def fake_context(db, workspace_id, engine):
        assert workspace_id == "workspace-a"
        return {
            "observed_at": "2026-11-16T00:00:00+00:00",
            "sources": {
                "tables": [f"{engine}_records"],
                "status": {f"{engine}_records": {"status": "available", "records": 1}},
                "partial": False,
                "unavailable_tables": [],
            },
            "evidence": {"sample": [{"id": engine}]},
            "execution_authority": "none",
        }

    class FakeGateway:
        async def complete_async(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            return SimpleNamespace(
                ok=True,
                text=f"advisory from {kwargs['caller']}",
                provider="test-provider",
                model="test-model",
                ai_receipt=None,
                request_id=f"request-{len(calls)}",
            )

        async def complete_async_structured(self, *_args, **_kwargs):
            return SimpleNamespace(ok=False, data=None, provider=None, model=None)

    monkeypatch.setattr(ai_chat, "_load_engine_context", fake_context)
    db = FakeCollaborationDB()
    reply, result, consultations, comparison, recommendation = asyncio.run(
        ai_chat._run_engine_collaboration(
            FakeGateway(), db, "workspace-a",
            "Investigate a reliability concern", ["guardian", "ai_brain"], "run-123"
        )
    )

    assert len(calls) == 7
    assert calls[0][1]["caller"] == "ai_chat"
    assert calls[1][1]["caller"] == "ai_chat"
    assert calls[2][1]["caller"] == "ai_chat"
    assert "Guardian consultation" in reply
    assert "Ai Brain consultation" in reply
    assert "advisory from ai_chat" in reply
    assert [item["engine"] for item in consultations] == ["guardian", "ai_brain"]
    assert all(item["status"] == "completed" for item in consultations)
    assert result.provider == "test-provider"
    assert db.evidence_summary["phase"] == "synthesizing"
    assert len(db.evidence_summary["progress_events"]) >= 5
    assert db.evidence_summary["progress_events"][-1]["engines"][0]["engine"] == "guardian"
    assert comparison["status"] == "unavailable"
    assert recommendation["status"] == "unavailable"


def test_collaboration_unavailable_consultation_is_not_a_negative_finding(monkeypatch):
    async def fake_context(db, workspace_id, engine):
        return {
            "observed_at": "2026-11-16T00:00:00+00:00",
            "sources": {"tables": [], "status": {}, "partial": True, "unavailable_tables": ["daily_summaries"]},
            "evidence": {},
            "execution_authority": "none",
        }

    class FakeGateway:
        async def complete_async_structured(self, *_args, **_kwargs):
            return SimpleNamespace(ok=False, data=None, provider=None, model=None)

        async def complete_async(self, prompt, **kwargs):
            if kwargs["max_tokens"] == 700:
                return SimpleNamespace(ok=False, text="", provider=None, model=None)
            return SimpleNamespace(
                ok=True, text="Synthesis notes unavailable consultation as unknown.",
                provider="test-provider", model="test-model", ai_receipt=None, request_id="final"
            )

    monkeypatch.setattr(ai_chat, "_load_engine_context", fake_context)
    db = FakeCollaborationDB()
    reply, _, consultations, _, _ = asyncio.run(
        ai_chat._run_engine_collaboration(
            FakeGateway(), db, "workspace-a", "Check security posture", ["guardian"], "run-456"
        )
    )
    assert consultations[0]["status"] == "unavailable"
    assert "not treated as a negative finding" in reply
