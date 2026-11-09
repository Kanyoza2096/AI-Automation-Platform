import base64
import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.unified_bus import UnifiedEventBus
from core.event_provenance_runtime import install_tenant_facebook_provenance
from services.v69_tenant_facebook_health_producer import PRODUCER_CLASS, PRODUCER_ID


KEY_ID = "KDO-TENANT-HEALTH-BOOT-01"
SEED = b"facebook-health-producer-seed!!"


class Query:
    def __init__(self, client):
        self.client = client
        self.columns = None
        self.row_limit = None

    def select(self, columns):
        self.columns = columns
        return self

    def limit(self, value):
        self.row_limit = value
        return self

    def execute(self):
        self.client.calls.append((self.columns, self.row_limit))
        if not self.client.ready:
            raise RuntimeError("column does not exist")
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    def table(self, name):
        assert name == "events"
        return Query(self)


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
    assert bus._provenance_signer == result.runtime.key_provider.sign
    assert bus._provenance_storage_enabled is True
    assert result.runtime.capability.principal.producer_id == PRODUCER_ID
    assert client.calls == [("provenance_envelope", 0)]


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
    assert client.calls == [("provenance_envelope", 0)]


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
    client = FakeSupabase(ready=True)
    bus = UnifiedEventBus(supabase_client=client)
    result = install_tenant_facebook_provenance(
        bus, environ=valid_environment()
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
