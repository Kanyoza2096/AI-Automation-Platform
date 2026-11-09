from types import SimpleNamespace

import pytest

import services.vnext_connector_fabric_evidence_read_model as evidence_model
from services.vnext_connector_fabric_evidence_read_model import (
    EvidenceReadError,
    build_connector_fabric_evidence,
)


class FakeQuery:
    def __init__(self, rows_by_table, table, fail_table=None):
        self.rows_by_table = rows_by_table
        self.table_name = table
        self.fail_table = fail_table

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.table_name == self.fail_table:
            raise RuntimeError("database detail must not leak")
        return SimpleNamespace(data=self.rows_by_table.get(self.table_name, []))


class FakeClient:
    def __init__(self, rows_by_table=None, fail_table=None):
        self.rows_by_table = rows_by_table or {}
        self.fail_table = fail_table

    def table(self, name):
        return FakeQuery(self.rows_by_table, name, self.fail_table)


def install_client(monkeypatch, client):
    monkeypatch.setattr(evidence_model, "_service_client", lambda: client)


def test_evidence_read_model_is_read_only_and_has_no_authority(monkeypatch):
    install_client(monkeypatch, FakeClient())
    model = build_connector_fabric_evidence("workspace-a", "github")
    assert model["readOnly"] is True
    assert model["authority"] == "none"
    assert model["eligibilityInputOnly"] is True
    assert model["executionEffect"] == "none"


def test_successful_empty_queries_are_explicitly_no_record(monkeypatch):
    install_client(monkeypatch, FakeClient())
    model = build_connector_fabric_evidence("workspace-a", "github")
    assert model["health"]["state"] == "NOT_EXPOSED"
    assert model["health"]["evidenceStatus"] == "no_record"
    assert model["certification"]["state"] == "NOT_EXPOSED"
    assert model["certification"]["evidenceStatus"] == "no_record"


def test_health_and_certification_rows_are_exposed_as_evidence_only(monkeypatch):
    install_client(monkeypatch, FakeClient({
        "connector_health_observations": [{
            "health_state": "degraded",
            "connection_id": "connection-a",
            "observed_at": "2026-11-09T10:00:00+00:00",
            "evidence_ref": "provider-check:abc",
        }],
        "connector_certification_registry": [{
            "registry_state": "active",
            "certification_status": "PASS",
            "live_provider_verified": True,
            "evidence_ref": "certification:xyz",
        }],
    }))
    model = build_connector_fabric_evidence("workspace-a", "github")
    assert model["health"]["state"] == "degraded"
    assert model["health"]["evidenceStatus"] == "available"
    assert model["certification"]["state"] == "active"
    assert model["certification"]["evidenceStatus"] == "available"
    assert model["authority"] == "none"


def test_database_query_failure_is_not_misreported_as_missing_evidence(monkeypatch):
    install_client(monkeypatch, FakeClient(fail_table="connector_health_observations"))
    with pytest.raises(EvidenceReadError, match="connector evidence query failed"):
        build_connector_fabric_evidence("workspace-a", "github")


def test_unavailable_service_client_is_not_misreported_as_empty_evidence(monkeypatch):
    install_client(monkeypatch, None)
    with pytest.raises(EvidenceReadError, match="client unavailable"):
        build_connector_fabric_evidence("workspace-a", "github")


def test_workspace_and_connector_are_required():
    with pytest.raises(ValueError, match="workspace_id is required"):
        build_connector_fabric_evidence("", "github")
    with pytest.raises(ValueError, match="connector_key is required"):
        build_connector_fabric_evidence("workspace-a", "")


def test_all_connector_records_remain_catalogued(monkeypatch):
    install_client(monkeypatch, FakeClient())
    for key in (
        "github", "render", "stripe", "shopify", "slack", "jira", "pagerduty",
        "datadog", "cloudflare", "twilio", "google_workspace", "aws",
        "facebook", "twitter", "linkedin", "instagram", "telegram", "whatsapp", "website",
    ):
        assert build_connector_fabric_evidence("w", key)["connector"]["connectorKey"] == key
