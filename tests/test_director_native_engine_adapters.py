import asyncio
from types import SimpleNamespace

import pytest

from services.director.orchestration.contracts import (
    AdapterRegistry,
    EngineAttemptReconciliation,
    ExecutionAttemptReference,
    EngineContribution,
    MissionTaskContext,
)
from services.director.orchestration.native_adapters import ContentIntelligenceMissionAdapter


def _context(**overrides):
    values = {
        "task_id": "task-1",
        "mission_id": "mission-1",
        "workspace_id": "workspace-1",
        "capability": "research.analyze",
        "objective": "Research the market",
        "inputs": {"topic": "Malawi SME market", "max_results": 6, "fetch_top": 3},
        "correlation_id": "corr-1",
        "attempt_id": "attempt-1",
    }
    values.update(overrides)
    return MissionTaskContext(**values)


def test_mission_task_context_requires_workspace_and_identity():
    with pytest.raises(ValueError, match="workspace_task_context_requires_workspace_id"):
        MissionTaskContext(
            task_id="task-1", mission_id="mission-1", workspace_id="",
            capability="research.analyze", objective="Research",
        )


def test_adapter_registry_requires_explicit_capability_and_unique_engine():
    adapter = ContentIntelligenceMissionAdapter(engine=object())
    registry = AdapterRegistry()
    registry.register(adapter)
    with pytest.raises(ValueError, match="adapter_already_registered"):
        registry.register(adapter)

    async def run():
        with pytest.raises(ValueError, match="capability_not_registered"):
            await registry.invoke("content_intelligence", _context(capability="publish.execute"))

    asyncio.run(run())


def test_content_intelligence_adapter_invokes_native_engine_with_workspace_and_evidence():
    class FakeEngine:
        def __init__(self):
            self.call = None

        async def analyze(self, topic, **kwargs):
            self.call = (topic, kwargs)
            return SimpleNamespace(to_dict=lambda: {
                "topic": topic,
                "mode": "full",
                "summary": "Three sourced findings.",
                "claims": ["Claim supported by source."],
                "recommendations": ["Investigate the cited market segment."],
                "risks": ["Source coverage is incomplete."],
                "evidence": [{
                    "fingerprint": "abc123",
                    "title": "Market report",
                    "url": "https://example.com/report",
                    "source": "web",
                    "snippet": "Sourced market evidence",
                }],
                "sources_count": 1,
                "providers": ["web"],
                "confidence": 0.82,
                "warnings": [],
            })

    engine = FakeEngine()
    adapter = ContentIntelligenceMissionAdapter(engine=engine)
    registry = AdapterRegistry()
    registry.register(adapter)

    result = asyncio.run(registry.invoke("content_intelligence", _context()))

    assert engine.call[0] == "Malawi SME market"
    assert engine.call[1]["workspace_id"] == "workspace-1"
    assert engine.call[1]["max_results"] == 6
    assert engine.call[1]["fetch_top"] == 3
    assert result.status == "completed"
    assert result.authority == "none"
    assert result.side_effect_class == "external_read_only"
    assert result.evidence[0]["ref"] == "abc123"
    assert result.findings == ["Claim supported by source."]


def test_content_intelligence_unavailable_is_not_a_negative_finding():
    class FakeEngine:
        async def analyze(self, topic, **kwargs):
            return SimpleNamespace(to_dict=lambda: {
                "topic": topic, "mode": "unavailable", "summary": "",
                "claims": [], "recommendations": [], "risks": [],
                "evidence": [], "sources_count": 0, "providers": [],
                "confidence": 0.0, "warnings": ["No sources available."],
            })

    adapter = ContentIntelligenceMissionAdapter(engine=FakeEngine())
    registry = AdapterRegistry()
    registry.register(adapter)
    result = asyncio.run(registry.invoke("content_intelligence", _context()))

    assert result.status == "unavailable"
    assert result.findings == []
    assert result.confidence == 0.0
    assert "No sources available." in result.limitations


def test_engine_contribution_cannot_grant_authority():
    with pytest.raises(ValueError, match="cannot_grant_authority"):
        EngineContribution(
            engine_id="content_intelligence",
            capability="research.analyze",
            task_id="task-1",
            mission_id="mission-1",
            workspace_id="workspace-1",
            status="completed",
            summary="Advisory only.",
            authority="execute",
        )



def test_adapter_without_authoritative_reconciliation_stays_unknown():
    registry = AdapterRegistry()
    registry.register(ContentIntelligenceMissionAdapter(engine=object()))
    reference = ExecutionAttemptReference(
        attempt_id="attempt-1", engine_id="content_intelligence",
        capability="research.analyze", task_id="task-1", mission_id="mission-1",
        workspace_id="workspace-1", correlation_id="corr-1",
    )

    result = asyncio.run(registry.reconcile_attempt(reference))

    assert result.status == "unknown"
    assert result.authoritative is False
    assert result.evidence_ref is None


def test_adapter_reconciliation_requires_exact_attempt_identity_and_evidence():
    class ReconciliableContentAdapter(ContentIntelligenceMissionAdapter):
        async def reconcile_attempt(self, reference):
            return EngineAttemptReconciliation(
                attempt_id=reference.attempt_id,
                engine_id=reference.engine_id,
                status="completed",
                authoritative=True,
                evidence_ref="engine-receipt:attempt-1",
                result={"summary": "The original attempt completed.", "completed_at": "2026-11-19T12:00:00+00:00"},
            )

    registry = AdapterRegistry()
    registry.register(ReconciliableContentAdapter(engine=object()))
    reference = ExecutionAttemptReference(
        attempt_id="attempt-1", engine_id="content_intelligence",
        capability="research.analyze", task_id="task-1", mission_id="mission-1",
        workspace_id="workspace-1", correlation_id="corr-1",
    )

    result = asyncio.run(registry.reconcile_attempt(reference))

    assert result.status == "completed"
    assert result.authoritative is True
    assert result.evidence_ref == "engine-receipt:attempt-1"



def test_adapter_reconciliation_identity_mismatch_fails_closed():
    class MismatchedContentAdapter(ContentIntelligenceMissionAdapter):
        async def reconcile_attempt(self, reference):
            return EngineAttemptReconciliation(
                attempt_id="different-attempt",
                engine_id=reference.engine_id,
                status="completed",
                authoritative=True,
                evidence_ref="engine-receipt:wrong-attempt",
                result={"summary": "Wrong attempt.", "completed_at": "2026-11-19T12:00:00+00:00"},
            )

    registry = AdapterRegistry()
    registry.register(MismatchedContentAdapter(engine=object()))
    reference = ExecutionAttemptReference(
        attempt_id="attempt-1", engine_id="content_intelligence",
        capability="research.analyze", task_id="task-1", mission_id="mission-1",
        workspace_id="workspace-1", correlation_id="corr-1",
    )

    result = asyncio.run(registry.reconcile_attempt(reference))

    assert result.status == "unknown"
    assert result.authoritative is False
