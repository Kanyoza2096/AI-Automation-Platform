import base64
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.unified_bus import UnifiedEventBus
from core.event_provenance_runtime import install_tenant_facebook_provenance
from services.v69_tenant_facebook_health_producer import (
    PRODUCER_CLASS,
    PRODUCER_ID,
    TenantHealthPublisherError,
)


KEY_ID = "KDO-TENANT-HEALTH-BOOT-01"
SEED = b"facebook-health-producer-seed!!!"


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.columns = None
        self.row_limit = None

    def select(self, columns):
        self.columns = columns
        return self

    def limit(self, value):
        self.row_limit = value
        return self

    def execute(self):
        self.client.calls.append((self.table_name, self.columns, self.row_limit))
        if self.table_name not in self.client.ready_tables:
            raise RuntimeError("table or column does not exist")
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self, ready=True, health_ready=False):
        self.ready_tables = set()
        if ready:
            self.ready_tables.add("events")
        if health_ready:
            self.ready_tables.update({
                "integration_connections",
                "integration_evidence",
                "connector_health_observations",
            })
        self.calls = []

    def table(self, name):
        return Query(self, name)


def valid_environment():
    private = Ed25519PrivateKey.from_private_bytes(SEED)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return {
        "KDO_EVENT_PROVENANCE_ACTIVE_SIGNING_KEY_ID": KEY_ID,
        "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_ID": PRODUCER_ID,
        "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_CLASS": PRODUCER_CLASS,
        "KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(SEED).decode(),
        "KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON": json.dumps({
            KEY_ID: base64.b64encode(public).decode()
        }),
        "KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON": json.dumps({
            KEY_ID: PRODUCER_ID
        }),
        "KDO_EVENT_PROVENANCE_REVOKED_KEY_IDS_JSON": "[]",
    }


def test_startup_installs_capability_on_existing_bus_only_after_read_only_schema_probe():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)

    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
    )

    assert result.enabled is True
    assert result.reason == "installed"
    assert result.runtime is not None
    assert bus._producer_registry is result.runtime.registry
    assert callable(bus._provenance_signer)
    assert result.runtime.active_key_id == KEY_ID
    assert not hasattr(result.runtime, "key_provider")
    assert bus._provenance_storage_enabled is True
    assert result.runtime.capability.principal.producer_id == PRODUCER_ID
    assert client.calls == [("events", "provenance_envelope", 0)]


def test_startup_does_not_enable_signing_when_migration_column_is_missing():
    client = FakeSupabase(ready=False)
    bus = UnifiedEventBus(supabase_client=client)

    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
    )

    assert result.enabled is False
    assert result.reason == "provenance_schema_not_ready"
    assert bus._producer_registry is None
    assert bus._provenance_signer is None
    assert bus._provenance_storage_enabled is False
    assert client.calls == [("events", "provenance_envelope", 0)]


def test_unconfigured_environment_does_not_probe_database_or_mutate_bus():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)

    result = install_tenant_facebook_provenance(bus, environ={})

    assert result.enabled is False
    assert result.reason == "key_configuration_absent"
    assert client.calls == []
    assert bus._producer_registry is None
    assert bus._provenance_storage_enabled is False


def test_partial_or_invalid_key_configuration_fails_closed():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)

    result = install_tenant_facebook_provenance(
        bus,
        environ={"KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON": "{}"},
    )

    assert result.enabled is False
    assert result.reason == "key_configuration_incomplete"
    assert client.calls == []
    assert bus._producer_registry is None


def test_startup_does_not_replace_or_reconfigure_an_already_owned_bus():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    original_registry = object()
    bus._producer_registry = original_registry

    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
    )

    assert result.enabled is False
    assert result.reason == "event_bus_provenance_already_owned"
    assert bus._producer_registry is original_registry
    assert client.calls == []


def test_startup_does_not_mutate_a_bus_after_dispatch_has_started():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    bus._running = True

    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
    )

    assert result.enabled is False
    assert result.reason == "event_bus_already_started"
    assert bus._producer_registry is None
    assert client.calls == []


def test_startup_installation_is_idempotent_and_reuses_same_runtime():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    env = valid_environment()

    first = install_tenant_facebook_provenance(bus, environ=env)
    second = install_tenant_facebook_provenance(bus, environ={})

    assert first.enabled and second.enabled
    assert second.reason == "already_installed"
    assert second.runtime is first.runtime
    assert len(client.calls) == 1


def test_active_key_must_belong_to_the_exact_tenant_facebook_producer():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    env = valid_environment()
    env["KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON"] = json.dumps({
        KEY_ID: "kdo-another-producer"
    })

    result = install_tenant_facebook_provenance(bus, environ=env)

    assert result.enabled is False
    assert result.reason == "key_configuration_invalid"
    assert client.calls == []
    assert bus._producer_registry is None



def test_runtime_factory_uses_the_same_canonical_bus_and_minted_capability():
    client = FakeSupabase(ready=True, health_ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    env = valid_environment()
    env["KDO_TENANT_FACEBOOK_HEALTH_EVIDENCE_READY"] = "true"
    result = install_tenant_facebook_provenance(
        bus, environ=env
    )

    producer = result.runtime.create_health_producer(
        resolver=object(),
        connector_factory=object(),
    )

    assert producer._event_bus is bus
    assert producer._producer_registry is result.runtime.registry
    assert producer._producer_capability is result.runtime.capability


def test_startup_rejects_a_different_supabase_client_from_the_bus():
    bus_client = FakeSupabase(ready=True)
    other_client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=bus_client)

    result = install_tenant_facebook_provenance(
        bus,
        supabase_client=other_client,
        environ=valid_environment(),
    )

    assert result.enabled is False
    assert result.reason == "canonical_event_database_mismatch"
    assert bus._producer_registry is None
    assert bus_client.calls == []
    assert other_client.calls == []



def test_facebook_producer_capability_cannot_sign_unrelated_event_types():
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
    )
    proof = result.runtime.registry.verify(
        result.runtime.capability,
        workspace_id="workspace-a",
        brand_id="brand-a",
    )

    with pytest.raises(ValueError, match="facebook_health_event_name_not_allowed"):
        bus._provenance_signer(
            proof,
            event_id="event-1",
            event_name="engine_alert",
            payload={"service": "api", "status": "degraded"},
            brand_id="brand-a",
        )



def test_health_producer_remains_disabled_without_operator_migration_attestation():
    client = FakeSupabase(ready=True, health_ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
    )

    assert result.enabled is True
    assert result.runtime.health_evidence_ready is False
    assert bus._tenant_health_evidence_ready is False
    with pytest.raises(TenantHealthPublisherError, match="health_evidence_schema_not_enabled"):
        result.runtime.create_health_producer(
            resolver=object(),
            connector_factory=object(),
        )
    # Without the explicit attestation, only the provenance column is probed.
    assert client.calls == [("events", "provenance_envelope", 0)]


def test_health_producer_requires_attestation_and_all_health_evidence_columns():
    client = FakeSupabase(ready=True, health_ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    env = valid_environment()
    env["KDO_TENANT_FACEBOOK_HEALTH_EVIDENCE_READY"] = "true"

    result = install_tenant_facebook_provenance(
        bus, environ=env
    )

    assert result.enabled is True
    assert result.runtime.health_evidence_ready is True
    assert bus._tenant_health_evidence_ready is True
    assert client.calls == [
        ("events", "provenance_envelope", 0),
        ("integration_connections", "id,workspace_id", 0),
        ("integration_evidence", "evidence_id,workspace_id,connection_id,sequence_no,metadata,evidence_hash", 0),
        ("connector_health_observations", "id,workspace_id,connection_id,connector_key,observed_at,health_state,evidence_ref", 0),
    ]


def test_health_evidence_attestation_is_not_enough_when_required_columns_are_missing():
    client = FakeSupabase(ready=True, health_ready=False)
    bus = UnifiedEventBus(supabase_client=client)
    env = valid_environment()
    env["KDO_TENANT_FACEBOOK_HEALTH_EVIDENCE_READY"] = "true"

    result = install_tenant_facebook_provenance(bus, environ=env)

    assert result.enabled is True
    assert result.runtime.health_evidence_ready is False
    assert bus._tenant_health_evidence_ready is False
    assert len(client.calls) == 2
