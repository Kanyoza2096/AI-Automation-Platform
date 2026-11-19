from pathlib import Path
from types import SimpleNamespace

import pytest

from services.autonomous_knowledge_evidence_reviewer import (
    AutonomousKnowledgeEvidenceReviewer,
    build_source_snapshot_attestation,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeGateway:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def complete_async_structured(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            ok=True, data=self.data, request_id="review-test",
            provider="test", model="test-model",
        )


def _entry(metadata=None):
    content = "The device measures temperature within the stated operating range."
    source_text = "The device measures temperature within the stated operating range."
    attestation = build_source_snapshot_attestation(
        secret="test-secret",
        workspace_id="workspace-1",
        source_id="manufacturer-doc-1",
        source_url="https://example.com/manual",
        trust_class="official_documentation",
        snapshot_text=source_text,
    )
    return SimpleNamespace(
        id="entry-1",
        workspace_id="workspace-1",
        content=content,
        metadata=attestation if metadata is None else metadata,
    )


def _case():
    return {
        "id": "case-1",
        "source_id": "manufacturer-doc-1",
        "source_url": "https://example.com/manual",
        "trust_class": "official_documentation",
    }


def _assessment(**overrides):
    data = {
        "decision": "approve",
        "confidence": 0.995,
        "claim_coverage": 1.0,
        "source_match": True,
        "claims": [{
            "claim": "The device measures temperature within the stated operating range.",
            "support_status": "supported",
            "source_ids": ["manufacturer-doc-1"],
            "rationale": "Directly stated in captured source text.",
        }],
        "rationale": "Every claim is directly supported by the captured source.",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_autonomous_reviewer_approves_only_when_all_local_gates_pass():
    gateway = FakeGateway(_assessment())
    result = await AutonomousKnowledgeEvidenceReviewer(gateway, snapshot_secret="test-secret").review(
        entry=_entry(), case=_case()
    )
    assert result["decision"] == "approved"
    assert result["confidence"] == 0.995
    assert result["evidence"]["source_id"] == "manufacturer-doc-1"
    assert result["evidence"]["source_snapshot_sha256"]


@pytest.mark.asyncio
async def test_missing_captured_source_snapshot_is_quarantined_without_llm_call():
    gateway = FakeGateway(_assessment())
    result = await AutonomousKnowledgeEvidenceReviewer(gateway).review(
        entry=_entry(metadata={}), case=_case()
    )
    assert result["decision"] == "quarantine"
    assert "captured source-text snapshot" in result["reason"]
    assert gateway.calls == 0


@pytest.mark.asyncio
async def test_forged_source_snapshot_metadata_is_quarantined():
    gateway = FakeGateway(_assessment())
    metadata = _entry().metadata
    metadata["source_snapshot_text"] = "Attacker supplied content."
    result = await AutonomousKnowledgeEvidenceReviewer(
        gateway, snapshot_secret="test-secret"
    ).review(entry=_entry(metadata=metadata), case=_case())
    assert result["decision"] == "quarantine"
    assert "signature" in result["reason"] or "provenance" in result["reason"]
    assert gateway.calls == 0


@pytest.mark.asyncio
async def test_uncertain_claim_cannot_be_auto_approved():
    gateway = FakeGateway(_assessment(claims=[{
        "claim": "A claim not directly established by the source.",
        "support_status": "uncertain",
        "source_ids": [],
        "rationale": "The source is inconclusive.",
    }]))
    result = await AutonomousKnowledgeEvidenceReviewer(gateway).review(
        entry=_entry(), case=_case()
    )
    assert result["decision"] == "quarantined"


@pytest.mark.asyncio
async def test_unknown_source_citation_fails_closed():
    gateway = FakeGateway(_assessment(claims=[{
        "claim": "A supported claim.",
        "support_status": "supported",
        "source_ids": ["forged-source-id"],
        "rationale": "Citation mismatch.",
    }]))
    result = await AutonomousKnowledgeEvidenceReviewer(gateway).review(
        entry=_entry(), case=_case()
    )
    assert result["decision"] == "quarantine"
    assert "unknown or missing source" in result["reason"]


@pytest.mark.asyncio
async def test_low_confidence_or_incomplete_coverage_cannot_auto_approve():
    gateway = FakeGateway(_assessment(confidence=0.91))
    result = await AutonomousKnowledgeEvidenceReviewer(gateway).review(
        entry=_entry(), case=_case()
    )
    assert result["decision"] == "quarantined"


def test_autonomous_review_is_wired_to_submission_and_promotion_is_gated():
    source = (ROOT / "api" / "routers" / "knowledge.py").read_text()
    assert "_run_autonomous_knowledge_evidence_review(" in source
    assert 'if decision == "approved":' in source
    assert '"p_actor_id": actor' in source
    assert '"review_mode": "autonomous"' in source


def test_quarantine_and_machine_review_audit_are_schema_supported():
    migration = (
        ROOT / "migrations" / "185_autonomous_knowledge_evidence_review.sql"
    ).read_text()
    assert "'quarantined'" in migration
    assert "review_confidence" in migration
    assert "review_evidence" in migration
    assert "review_mode" in migration
    assert "REVOKE ALL ON FUNCTION public.record_knowledge_evidence_review_event()" in migration
