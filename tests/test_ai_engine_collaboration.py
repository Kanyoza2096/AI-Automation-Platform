import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers import ai_chat


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

    monkeypatch.setattr(ai_chat, "_load_engine_context", fake_context)
    reply, result, consultations = asyncio.run(
        ai_chat._run_engine_collaboration(
            FakeGateway(), object(), "workspace-a",
            "Investigate a reliability concern", ["guardian", "ai_brain"]
        )
    )

    assert len(calls) == 3
    assert calls[0][1]["caller"] == "ai_chat"
    assert calls[1][1]["caller"] == "ai_chat"
    assert calls[2][1]["caller"] == "ai_chat"
    assert "Guardian consultation" in reply
    assert "Ai Brain consultation" in reply
    assert "advisory from ai_engine_collaboration_director" in reply
    assert [item["engine"] for item in consultations] == ["guardian", "ai_brain"]
    assert all(item["status"] == "completed" for item in consultations)
    assert result.provider == "test-provider"


def test_collaboration_unavailable_consultation_is_not_a_negative_finding(monkeypatch):
    async def fake_context(db, workspace_id, engine):
        return {
            "observed_at": "2026-11-16T00:00:00+00:00",
            "sources": {"tables": [], "status": {}, "partial": True, "unavailable_tables": ["daily_summaries"]},
            "evidence": {},
            "execution_authority": "none",
        }

    class FakeGateway:
        async def complete_async(self, prompt, **kwargs):
            if kwargs["caller"] == "ai_chat":
                return SimpleNamespace(ok=False, text="", provider=None, model=None)
            return SimpleNamespace(
                ok=True, text="Synthesis notes unavailable consultation as unknown.",
                provider="test-provider", model="test-model", ai_receipt=None, request_id="final"
            )

    monkeypatch.setattr(ai_chat, "_load_engine_context", fake_context)
    reply, _, consultations = asyncio.run(
        ai_chat._run_engine_collaboration(
            FakeGateway(), object(), "workspace-a", "Check security posture", ["guardian"]
        )
    )
    assert consultations[0]["status"] == "unavailable"
    assert "not treated as a negative finding" in reply
