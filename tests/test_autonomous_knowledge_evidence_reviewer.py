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
    result = await AutonomousKnowledgeEvidenceReviewer(gateway, snapshot_secret="test-secret").review(
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
    result = await AutonomousKnowledgeEvidenceReviewer(gateway, snapshot_secret="test-secret").review(
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
    result = await AutonomousKnowledgeEvidenceReviewer(gateway, snapshot_secret="test-secret").review(
        entry=_entry(), case=_case()
    )
    assert result["decision"] == "quarantine"
    assert "unknown or missing source" in result["reason"]


@pytest.mark.asyncio
async def test_low_confidence_or_incomplete_coverage_cannot_auto_approve():
    gateway = FakeGateway(_assessment(confidence=0.91))
    result = await AutonomousKnowledgeEvidenceReviewer(gateway, snapshot_secret="test-secret").review(
        entry=_entry(), case=_case()
    )
    assert result["decision"] == "quarantined"


def test_knowledge_ingestion_uses_lightweight_checks_not_case_management():
    source = (ROOT / "api" / "routers" / "knowledge.py").read_text()
    create_start = source.index("async def create_knowledge_entry")
    create_end = source.index('@router.get("/brands/{brand_id}/knowledge")', create_start)
    create_route = source[create_start:create_end]
    bulk_start = source.index("async def bulk_create_knowledge")
    bulk_end = source.index('@router.get("/knowledge")', bulk_start)
    bulk_route = source[bulk_start:bulk_end]
    assert "check_knowledge_entry(" in create_route
    assert "check_knowledge_entry(" in bulk_route
    assert "_autonomously_review_new_entry(" not in create_route
    assert "_autonomously_review_new_entry(" not in bulk_route
    assert '"kdo_lightweight_review"' in create_route
    assert '"kdo_lightweight_review"' in bulk_route
    assert '"verified": True' not in create_route
    assert '"verified": True' not in bulk_route


def test_full_evidence_case_management_is_opt_in_and_disabled_by_default():
    source = (ROOT / "api" / "routers" / "knowledge.py").read_text()
    assert 'os.getenv("KDO_KB_EVIDENCE_REVIEW_MODE", "lightweight")' in source
    assert "def _require_full_evidence_review_mode()" in source
    assert "_require_full_evidence_review_mode()" in source
    assert "knowledge_review_scheduler_disabled_in_lightweight_mode" in source


def test_trusted_ingestion_uses_server_fetch_and_operator_host_registry():
    source = (ROOT / "api" / "routers" / "knowledge.py").read_text()
    assert '@router.post("/brands/{brand_id}/knowledge/ingest-source")' in source
    assert "web.fetch_async(source_url)" in source
    assert "build_source_snapshot_attestation(" in source
    service = (ROOT / "services" / "autonomous_knowledge_evidence_reviewer.py").read_text()
    assert "KDO_KB_TRUSTED_SOURCE_DOMAINS" in service
    route = source.split('@router.post("/brands/{brand_id}/knowledge/ingest-source")', 1)[1].split('@router.post("/brands/{brand_id}/knowledge/{knowledge_id}/evidence-review")', 1)[0]
    assert "body.trust_class" not in route
    assert "KDO_KB_SOURCE_SNAPSHOT_HMAC_SECRET" in source


def test_source_snapshot_attestation_can_be_reused_only_when_every_binding_matches():
    from services.autonomous_knowledge_evidence_reviewer import (
        build_source_snapshot_attestation,
        verify_source_snapshot_attestation,
    )

    metadata = {
        "source_id": "web-source:abc",
        "source_url": "https://who.int/health",
        "trust_class": "government",
        **build_source_snapshot_attestation(
            secret="test-secret",
            workspace_id="workspace-1",
            source_id="web-source:abc",
            source_url="https://who.int/health",
            trust_class="government",
            snapshot_text="Captured source text.",
        ),
    }
    assert verify_source_snapshot_attestation(metadata, "workspace-1", "test-secret")
    assert not verify_source_snapshot_attestation(metadata, "workspace-2", "test-secret")
    tampered = {**metadata, "source_snapshot_text": "Tampered source text."}
    assert not verify_source_snapshot_attestation(tampered, "workspace-1", "test-secret")


def test_sweep_scheduler_trigger_is_secret_gated_and_backgrounded():
    source = (ROOT / "api" / "routers" / "knowledge.py").read_text()
    assert '@router.post("/internal/knowledge-evidence-review/sweep")' in source
    assert "KDO_KB_REVIEW_SWEEP_TRIGGER_TOKEN" in source
    assert "hmac.compare_digest(expected, supplied)" in source
    assert "asyncio.create_task(_run_review_sweep_background())" in source
    assert "await asyncio.to_thread(_run_in_thread)" in source
    main = (ROOT / "main.py").read_text()
    assert "await start_knowledge_review_sweep_scheduler()" in main
    assert "await stop_knowledge_review_sweep_scheduler()" in main
    assert "async def stop_knowledge_review_sweep_scheduler" in source
    assert "async def _periodic_knowledge_review_sweep" in source
    assert "_review_sweep_task = asyncio.create_task(_run_review_sweep_background())" in source
    assert 'result.get("discovered") == 0 and result.get("reached_end")' in source
    sweep = (ROOT / "scripts" / "autonomous_knowledge_evidence_review.py").read_text()
    assert 'counters["reached_end"] = int(reached_end)' in sweep


def test_backlog_sweep_reopens_only_when_trusted_snapshot_changes():
    source = (ROOT / "scripts" / "autonomous_knowledge_evidence_review.py").read_text()
    assert "current_snapshot_digest != prior_snapshot_digest" in source
    assert '"source_snapshot_sha256": (' in source
    assert "entry.metadata.get(\"source_snapshot_sha256\")" in source


def test_backlog_sweep_captures_allowlisted_sources_before_case_review():
    source = (ROOT / "scripts" / "autonomous_knowledge_evidence_review.py").read_text()
    assert "async def _capture_trusted_source_snapshot" in source
    assert "resolve_trusted_source_class(source_url)" in source
    assert "build_source_snapshot_attestation(" in source
    assert "await _capture_trusted_source_snapshot(client, row)" in source
    assert "prior_case.data[0].get(\"review_status\") == \"approved\"" in source


def test_trust_class_registry_uses_exact_host_and_allowlisted_values(monkeypatch):
    import json
    from services.autonomous_knowledge_evidence_reviewer import resolve_trusted_source_class

    registry = json.dumps({"who.int": "government", "docs.vendor.example": "official_documentation"})
    assert resolve_trusted_source_class("https://who.int/health", registry) == "government"
    assert resolve_trusted_source_class("https://docs.vendor.example/manual", registry) == "official_documentation"
    with pytest.raises(ValueError):
        resolve_trusted_source_class("https://sub.who.int/health", registry)
    with pytest.raises(ValueError):
        resolve_trusted_source_class("https://attacker.example/who.int", registry)

def test_additional_trusted_source_registry_is_additive_and_exact_host(monkeypatch):
    import json
    from services.autonomous_knowledge_evidence_reviewer import resolve_trusted_source_class

    monkeypatch.setenv(
        "KDO_KB_TRUSTED_SOURCE_DOMAINS_ADDITIONAL",
        json.dumps({
            "hld.handbook.academy": "publisher_primary",
            "ai-eng.handbook.academy": "publisher_primary",
            "dsa.handbook.academy": "publisher_primary",
        }),
    )
    existing = json.dumps({"who.int": "government"})
    assert resolve_trusted_source_class("https://who.int/health", existing) == "government"
    assert resolve_trusted_source_class(
        "https://hld.handbook.academy/chapter", existing
    ) == "publisher_primary"
    with pytest.raises(ValueError):
        resolve_trusted_source_class("https://sub.hld.handbook.academy/chapter", existing)


def test_additional_trusted_source_registry_rejects_conflicting_classification(monkeypatch):
    import json
    from services.autonomous_knowledge_evidence_reviewer import resolve_trusted_source_class

    monkeypatch.setenv(
        "KDO_KB_TRUSTED_SOURCE_DOMAINS_ADDITIONAL",
        json.dumps({"who.int": "publisher_primary"}),
    )
    with pytest.raises(ValueError, match="conflicting"):
        resolve_trusted_source_class(
            "https://who.int/health", json.dumps({"who.int": "government"})
        )


@pytest.mark.asyncio
async def test_backlog_sweep_drains_pending_cases_before_cursor_scan(monkeypatch):
    from types import SimpleNamespace
    import scripts.autonomous_knowledge_evidence_review as sweep

    pending_case = {
        "id": "case-pending",
        "knowledge_entry_id": "entry-old",
        "workspace_id": "workspace-1",
        "brand_id": "brand-1",
        "review_status": "pending",
    }
    entry_row = {
        "id": "entry-old",
        "content": "An older knowledge entry awaiting review.",
        "workspace_id": "workspace-1",
        "brand_id": "brand-1",
        "doc_type": "text",
        "metadata": {},
    }

    class FakeQuery:
        def __init__(self, table):
            self.table = table
        def select(self, *_args, **_kwargs): return self
        def eq(self, *_args, **_kwargs): return self
        def order(self, *_args, **_kwargs): return self
        def limit(self, *_args, **_kwargs): return self
        def execute(self):
            return SimpleNamespace(data=[pending_case] if self.table == "knowledge_evidence_review_cases" else [entry_row])

    class FakeClient:
        def table(self, name): return FakeQuery(name)

    reviewed = []
    refreshed_case = {
        **pending_case,
        "source_id": "source-recovered-1",
        "source_url": "https://trusted.example/source",
        "trust_class": "official_documentation",
    }
    async def capture(_client, row):
        return {
            **row["metadata"],
            "source_snapshot_text": "Trusted captured source text",
            "source_snapshot_sha256": "snapshot-digest",
        }
    def refresh_case(_client, row):
        assert row["metadata"]["source_snapshot_text"] == "Trusted captured source text"
        return refreshed_case
    async def review(_client, case, row):
        reviewed.append((case["id"], case.get("source_url"), row["id"]))
        return "quarantined"

    monkeypatch.setattr(sweep, "_capture_trusted_source_snapshot", capture)
    monkeypatch.setattr(sweep, "_case_for_entry", refresh_case)
    monkeypatch.setattr(sweep, "_review_case", review)
    counters = {
        "scanned": 0, "discovered": 0, "approved": 0,
        "rejected": 0, "quarantined": 0, "raced": 0, "skipped": 0,
    }

    await sweep._drain_pending_review_cases(FakeClient(), 10, counters)

    assert reviewed == [("case-pending", "https://trusted.example/source", "entry-old")]
    assert counters["discovered"] == 1
    assert counters["quarantined"] == 1
