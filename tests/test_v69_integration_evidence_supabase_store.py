from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.v69_integration_evidence import (
    EvidenceError,
    IntegrationEvidence,
    SupabaseIntegrationEvidenceStore,
    emit,
    payload_fingerprint,
)


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


class FakeQuery:
    def __init__(self, client, workspace_id, connection_id):
        self.client = client
        self.workspace_id = workspace_id
        self.connection_id = connection_id

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        if field == "workspace_id":
            self.workspace_id = value
        elif field == "connection_id":
            self.connection_id = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.client.fail_head:
            raise RuntimeError("database unavailable")
        head = self.client.heads.get((self.workspace_id, self.connection_id), "")
        return SimpleNamespace(data=[{"evidence_hash": head}] if head else [])


class FakeRPC:
    def __init__(self, client, name, params):
        self.client = client
        self.name = name
        self.params = params

    def execute(self):
        self.client.rpc_calls.append(dict(self.params))
        if self.client.conflict_next:
            self.client.conflict_next = False
            self.client.heads[(
                self.params["p_workspace_id"],
                self.params["p_connection_id"],
            )] = "b" * 64
            raise RuntimeError("evidence_chain_conflict")
        if self.client.fail_rpc:
            raise RuntimeError("database write failed")
        key = (self.params["p_workspace_id"], self.params["p_connection_id"])
        expected = self.client.heads.get(key, "")
        if self.params["p_expected_previous_hash"] != expected:
            raise RuntimeError("evidence_chain_conflict")
        self.client.heads[key] = self.params["p_evidence_hash"]
        if self.client.unconfirmed:
            return SimpleNamespace(data=[])
        receipt = {"evidence_id": self.params["p_evidence_id"]}
        if self.name == "record_connector_health_observation":
            receipt["observation_id"] = "observation-1"
        return SimpleNamespace(data=[receipt])


class FakeClient:
    def __init__(self):
        self.heads = {}
        self.rpc_calls = []
        self.fail_head = False
        self.fail_rpc = False
        self.conflict_next = False
        self.unconfirmed = False

    def table(self, _table):
        return FakeQuery(self, "", "")

    def rpc(self, name, params):
        return FakeRPC(self, name, params)


def make_store(client=None):
    return SupabaseIntegrationEvidenceStore(client or FakeClient())


def test_supabase_sink_persists_evidence_with_tenant_scoped_hash_chain():
    client = FakeClient()
    store = make_store(client)

    first = emit(
        store, evidence_id="evidence-1", workspace_id="workspace-a",
        connection_id="connection-a", connector_key="facebook",
        event_type="health.checked", outcome="healthy", occurred_at=NOW,
        metadata={
            "access_token": "never-store-this",
            "page_access_token": "page-secret-must-not-persist",
            "latency_ms": 18,
        },
    )
    second = emit(
        store, evidence_id="evidence-2", workspace_id="workspace-a",
        connection_id="connection-a", connector_key="facebook",
        event_type="health.checked", outcome="healthy", occurred_at=NOW,
    )

    assert second.previous_hash == first.evidence_hash
    assert client.heads[("workspace-a", "connection-a")] == second.evidence_hash
    assert client.rpc_calls[0]["p_metadata"]["access_token"] == "<redacted>"
    assert client.rpc_calls[0]["p_metadata"]["page_access_token"] == "<redacted>"
    assert "page-secret-must-not-persist" not in repr(client.rpc_calls[0]["p_metadata"])
    assert len(client.rpc_calls) == 2


def test_supabase_sink_retries_when_another_writer_advances_the_head():
    client = FakeClient()
    client.conflict_next = True
    store = make_store(client)

    evidence = emit(
        store, evidence_id="evidence-race", workspace_id="workspace-a",
        connection_id="connection-a", connector_key="facebook",
        event_type="health.checked", outcome="healthy", occurred_at=NOW,
    )

    assert len(client.rpc_calls) == 2
    assert evidence.previous_hash == "b" * 64
    assert client.heads[("workspace-a", "connection-a")] == evidence.evidence_hash


def test_supabase_sink_fails_closed_when_head_cannot_be_read():
    client = FakeClient()
    client.fail_head = True
    store = make_store(client)

    with pytest.raises(EvidenceError, match="evidence_head_read_failed"):
        emit(
            store, evidence_id="evidence-read-fail", workspace_id="workspace-a",
            connection_id="connection-a", connector_key="facebook",
            event_type="health.checked", outcome="unknown", occurred_at=NOW,
        )


def test_supabase_sink_fails_closed_when_atomic_append_fails():
    client = FakeClient()
    client.fail_rpc = True
    store = make_store(client)

    with pytest.raises(EvidenceError, match="evidence_persist_failed"):
        emit(
            store, evidence_id="evidence-write-fail", workspace_id="workspace-a",
            connection_id="connection-a", connector_key="facebook",
            event_type="health.checked", outcome="unknown", occurred_at=NOW,
        )


def test_supabase_sink_requires_database_append_receipt():
    client = FakeClient()
    client.unconfirmed = True
    store = make_store(client)

    with pytest.raises(EvidenceError, match="evidence_persist_unconfirmed"):
        emit(
            store, evidence_id="evidence-unconfirmed", workspace_id="workspace-a",
            connection_id="connection-a", connector_key="facebook",
            event_type="health.checked", outcome="unknown", occurred_at=NOW,
        )


def test_supabase_sink_requires_explicit_database_client():
    with pytest.raises(EvidenceError, match="evidence_database_client_required"):
        SupabaseIntegrationEvidenceStore(None)


def test_supabase_sink_rejects_forged_evidence_hash_before_rpc():
    client = FakeClient()
    store = make_store(client)
    forged = IntegrationEvidence(
        evidence_id="forged-evidence",
        workspace_id="workspace-a",
        connection_id="connection-a",
        connector_key="facebook",
        event_type="health.checked",
        occurred_at=NOW,
        outcome="healthy",
        payload_fingerprint=payload_fingerprint({}),
        evidence_hash="f" * 64,
    )

    with pytest.raises(EvidenceError, match="evidence_hash_mismatch"):
        store.append(forged)
    assert client.rpc_calls == []


def test_supabase_sink_atomically_records_health_observation_and_evidence():
    client = FakeClient()
    store = make_store(client)

    result = store.record_health_observation(
        workspace_id="workspace-a",
        connection_id="connection-a",
        connector_key="facebook",
        health_state="healthy",
        observed_at=NOW,
        latency_ms=15,
        provider_status_code=200,
        provider="facebook",
        connector_version="1.0.0",
        metadata={"page_access_token": "never-store-this"},
        evidence_id="health-evidence-1",
    )

    assert result.evidence_id == "health-evidence-1"
    assert result.observation_id == "observation-1"
    assert result.workspace_id == "workspace-a"
    assert result.connection_id == "connection-a"
    assert result.health_state == "healthy"
    assert client.rpc_calls[0]["p_health_state"] == "healthy"
    assert client.rpc_calls[0]["p_observed_at"] == NOW.isoformat()
    assert client.rpc_calls[0]["p_metadata"]["page_access_token"] == "<redacted>"
    assert "p_event_type" not in client.rpc_calls[0]


def test_supabase_health_observation_retries_concurrent_evidence_head():
    client = FakeClient()
    client.conflict_next = True
    store = make_store(client)

    result = store.record_health_observation(
        workspace_id="workspace-a",
        connection_id="connection-a",
        connector_key="facebook",
        health_state="unknown",
        observed_at=NOW,
        reason_codes=("provider_check_not_healthy",),
        evidence_id="health-race",
    )

    assert result.evidence_id == "health-race"
    assert len(client.rpc_calls) == 2
    assert client.rpc_calls[1]["p_expected_previous_hash"] == "b" * 64


def test_supabase_health_observation_rejects_invalid_state_and_latency():
    store = make_store()

    with pytest.raises(EvidenceError, match="connector_health_state_invalid"):
        store.record_health_observation(
            workspace_id="workspace-a", connection_id="connection-a",
            connector_key="facebook", health_state="stuck",
        )
    with pytest.raises(EvidenceError, match="connector_health_latency_invalid"):
        store.record_health_observation(
            workspace_id="workspace-a", connection_id="connection-a",
            connector_key="facebook", health_state="healthy", latency_ms=-1,
        )


def test_supabase_health_observation_requires_rpc_receipt():
    client = FakeClient()
    client.unconfirmed = True
    store = make_store(client)

    with pytest.raises(EvidenceError, match="health_observation_persist_unconfirmed"):
        store.record_health_observation(
            workspace_id="workspace-a", connection_id="connection-a",
            connector_key="facebook", health_state="healthy", observed_at=NOW,
        )
