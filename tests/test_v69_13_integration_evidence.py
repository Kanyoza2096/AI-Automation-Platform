from datetime import datetime, timezone

import pytest

from services.v69_integration_evidence import (
    EvidenceError,
    MemoryEvidenceStore,
    compute_evidence_hash,
    emit,
    payload_fingerprint,
)


NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def test_evidence_is_tenant_scoped_and_hash_chained():
    store = MemoryEvidenceStore()
    first = emit(store, evidence_id="e1", workspace_id="w1", connection_id="c1", connector_key="facebook", event_type="runtime_block", outcome="blocked", occurred_at=NOW, reason_codes=("not_ready",))
    second = emit(store, evidence_id="e2", workspace_id="w1", connection_id="c1", connector_key="facebook", event_type="health_observation", outcome="ok", occurred_at=NOW, metadata={"latency_ms": 12})
    assert second.previous_hash == first.evidence_hash
    assert second.evidence_hash == compute_evidence_hash(second)
    assert len(store.list("w1", "c1")) == 2
    assert store.list("w2") == ()


def test_payload_is_fingerprinted_and_secrets_redacted_from_metadata():
    store = MemoryEvidenceStore()
    e = emit(
        store, evidence_id="e1", workspace_id="w1", connection_id="c1", connector_key="facebook",
        event_type="ingress_reject", outcome="blocked", occurred_at=NOW,
        payload={"message": "hello", "token": "super-secret"},
        metadata={"access_token": "super-secret", "reason": "bad_signature"},
    )
    assert e.payload_fingerprint == payload_fingerprint({"message": "hello", "token": "super-secret"})
    assert e.metadata["access_token"] == "<redacted>"
    assert "super-secret" not in repr(e.metadata)


def test_evidence_cannot_grant_authority_or_execution_effect():
    store = MemoryEvidenceStore()
    e = emit(store, evidence_id="e1", workspace_id="w1", connection_id="c1", connector_key="facebook", event_type="runtime_allow", outcome="allowed", occurred_at=NOW)
    assert e.authority_granted is False
    assert e.execution_effect == "none"


def test_duplicate_id_and_chain_tampering_fail_closed():
    store = MemoryEvidenceStore()
    e = emit(store, evidence_id="e1", workspace_id="w1", connection_id="c1", connector_key="facebook", event_type="x", outcome="ok", occurred_at=NOW)
    with pytest.raises(EvidenceError, match="duplicate_evidence_id"):
        store.append(e)
    bad = type(e)(**{**e.__dict__, "evidence_id": "e2", "previous_hash": "forged"})
    with pytest.raises(EvidenceError, match="evidence_chain_mismatch"):
        store.append(bad)
