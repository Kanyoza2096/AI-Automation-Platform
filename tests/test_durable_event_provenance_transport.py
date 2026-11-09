"""End-to-end transport plumbing tests for signed durable event provenance.

The tests use injected signer/verifier functions so they exercise the transport
boundaries without creating real producer keys or touching a database.
Cryptographic behavior remains covered by test_durable_event_provenance.py.
"""
from __future__ import annotations

import asyncio
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.durable_event_provenance import sign_durable_provenance, verify_durable_provenance
from core.event_provenance import ProducerPrincipal, ProducerRegistry, VerifiedEventProvenance
from core.shared_event_bus import SharedEventBus
from core.unified_bus import EventBusConfig, UnifiedEventBus


class _Result:
    data = [{"id": "event-1"}]


class _InsertQuery:
    def __init__(self, db):
        self.db = db

    def insert(self, record):
        self.db.record = record
        return self

    def execute(self):
        return _Result()


class _UpsertQuery:
    def __init__(self, db):
        self.db = db

    def upsert(self, records, **kwargs):
        self.db.records = records
        self.db.upsert_options = kwargs
        return self

    def execute(self):
        return _Result()


class _SharedDB:
    def __init__(self):
        self.record = None

    def table(self, name):
        assert name == "events"
        return _InsertQuery(self)


class _UnifiedDB:
    def __init__(self):
        self.records = None
        self.upsert_options = None

    def table(self, name):
        assert name == "events"
        return _UpsertQuery(self)


def _proof(workspace_id="workspace-a", brand_id="brand-a"):
    return VerifiedEventProvenance(
        producer=ProducerPrincipal("health-producer", "tenant_health"),
        actor=None,
        workspace_id=workspace_id,
        brand_id=brand_id,
        verification="ed25519_v1",
    )


def test_shared_bus_persists_signed_envelope_outside_payload():
    db = _SharedDB()
    registry = ProducerRegistry({"health-producer": "tenant_health"})
    signed = {"version": 1, "signature": "test-signature"}
    bus = SharedEventBus(
        db,
        source_service="health-worker",
        producer_registry=registry,
        provenance_signer=lambda proof, **kwargs: signed,
        provenance_storage_enabled=True,
    )

    assert bus.publish(
        "health.observed",
        {"service": "api", "status": "degraded"},
        workspace_id="workspace-a",
        brand_id="brand-a",
        producer_capability=registry.issue("health-producer"),
    )

    assert db.record["provenance_envelope"] == signed
    assert "provenance" not in db.record["payload"]
    assert db.record["workspace_id"] == "workspace-a"
    assert db.record["brand_id"] == "brand-a"


def test_shared_bus_rejects_capability_when_durable_signing_is_not_configured():
    db = _SharedDB()
    registry = ProducerRegistry({"health-producer": "tenant_health"})
    bus = SharedEventBus(
        db,
        source_service="health-worker",
        producer_registry=registry,
        provenance_storage_enabled=True,
    )

    assert bus.publish(
        "health.observed",
        {"service": "api", "status": "degraded"},
        workspace_id="workspace-a",
        producer_capability=registry.issue("health-producer"),
    ) is False
    assert db.record is None


def test_shared_bus_context_handler_receives_only_independently_verified_proof(monkeypatch):
    verified = _proof()
    verifier_calls = []
    bus = SharedEventBus(
        source_service="consumer",
        provenance_verifier=lambda envelope, **kwargs: (
            verifier_calls.append((envelope, kwargs)) or verified
        ),
    )
    received = []
    bus.subscribe_with_context(
        "health.observed",
        received.append,
        handler_id="tests:health-context",
    )
    monkeypatch.setattr(
        bus, "_ensure_handler_delivery",
        lambda *_args: {"id": "handler-receipt", "processed": False},
    )
    monkeypatch.setattr(
        bus, "_claim_handler_delivery",
        lambda _receipt: ("handler-receipt", "handler-claim"),
    )

    class _Heartbeat:
        def join(self, timeout=None):
            return None

    monkeypatch.setattr(
        bus, "_start_handler_lease_heartbeat",
        lambda *_args: (threading.Event(), _Heartbeat()),
    )
    monkeypatch.setattr(bus, "_mark_handler_delivery_processed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bus, "_mark_delivery_processed", lambda *_args, **_kwargs: True)

    event = {
        "id": "event-1",
        "event_type": "health.observed",
        "workspace_id": "workspace-a",
        "brand_id": "brand-a",
        "source_service": "caller-controlled-label",
        "source_instance": "remote-process",
        "payload": {"service": "api", "status": "degraded"},
        "provenance_envelope": {"signature": "signed"},
    }
    bus._dispatch_event(event, delivery_id="delivery-1", claim_token="group-claim")

    assert len(verifier_calls) == 1
    assert verifier_calls[0][1]["event_id"] == "event-1"
    assert verifier_calls[0][1]["workspace_id"] == "workspace-a"
    assert len(received) == 1
    assert received[0]["provenance"] is verified
    assert received[0]["payload"] == event["payload"]
    assert "provenance" not in received[0]["payload"]


def test_shared_bus_invalid_signature_reaches_context_handler_as_untrusted(monkeypatch):
    bus = SharedEventBus(
        source_service="consumer",
        provenance_verifier=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad signature")),
    )
    received = []
    bus.subscribe_with_context(
        "health.observed",
        received.append,
        handler_id="tests:untrusted-context",
    )
    monkeypatch.setattr(bus, "_ensure_handler_delivery", lambda *_args: {"id": "receipt", "processed": False})
    monkeypatch.setattr(bus, "_claim_handler_delivery", lambda _receipt: ("receipt", "claim"))

    class _Heartbeat:
        def join(self, timeout=None):
            return None

    monkeypatch.setattr(bus, "_start_handler_lease_heartbeat", lambda *_args: (threading.Event(), _Heartbeat()))
    monkeypatch.setattr(bus, "_mark_handler_delivery_processed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bus, "_mark_delivery_processed", lambda *_args, **_kwargs: True)

    bus._dispatch_event({
        "id": "event-2",
        "event_type": "health.observed",
        "workspace_id": "workspace-a",
        "brand_id": None,
        "payload": {"service": "api", "status": "degraded"},
        "provenance_envelope": {"signature": "tampered"},
    }, delivery_id="delivery-2", claim_token="group-claim")

    assert len(received) == 1
    assert received[0]["provenance"] is None


def test_unified_bus_persists_signed_envelope_in_dedicated_column():
    async def run():
        db = _UnifiedDB()
        registry = ProducerRegistry({"health-producer": "tenant_health"})
        signed = {"version": 1, "signature": "test-signature"}
        bus = UnifiedEventBus(
            supabase_client=db,
            config=EventBusConfig(persistence_flush_interval=0.01),
            producer_registry=registry,
            provenance_signer=lambda proof, **kwargs: signed,
            provenance_storage_enabled=True,
        )
        event_id = await bus.publish(
            "engine_alert",
            {"service": "api", "status": "degraded"},
            source="caller-controlled-label",
            workspace_id="workspace-a",
            brand_id="brand-a",
            producer_capability=registry.issue("health-producer"),
        )
        assert event_id
        assert db.records is not None
        assert db.records[0]["id"] == event_id
        assert db.records[0]["provenance_envelope"] == signed
        assert "provenance" not in db.records[0]["payload"]
        await bus.stop()

    asyncio.run(run())


def test_unified_bus_legacy_persistence_omits_new_column_until_signed():
    async def run():
        db = _UnifiedDB()
        bus = UnifiedEventBus(supabase_client=db)
        event_id = await bus.publish(
            "engine_alert",
            {"service": "api", "status": "degraded"},
            workspace_id="workspace-a",
        )
        assert event_id
        assert db.records is not None
        assert "provenance_envelope" not in db.records[0]
        await bus.stop()

    asyncio.run(run())



def test_shared_bus_round_trips_real_ed25519_proof_for_historical_event(monkeypatch):
    private_seed = b"historical-producer-key-32-bytes!"[:32]
    private_key = Ed25519PrivateKey.from_private_bytes(private_seed)
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    registry = ProducerRegistry({"health-producer": "tenant_health"})

    def signer(proof, *, event_id, event_name, payload, brand_id):
        return sign_durable_provenance(
            proof,
            event_id=event_id,
            event_name=event_name,
            payload=payload,
            key_id="health-producer-v1",
            signing_key=private_seed,
            issued_at=1_000,
            ttl_seconds=60,
        )

    db = _SharedDB()
    publisher = SharedEventBus(
        db,
        source_service="health-worker",
        producer_registry=registry,
        provenance_signer=signer,
        provenance_storage_enabled=True,
    )
    assert publisher.publish(
        "health.observed",
        {"service": "api", "status": "degraded"},
        workspace_id="workspace-a",
        brand_id="brand-a",
        producer_capability=registry.issue("health-producer"),
    )

    def verifier(envelope, **event):
        return verify_durable_provenance(
            envelope,
            keyring={"health-producer-v1": public_key},
            key_owners={"health-producer-v1": "health-producer"},
            now=5_000,
            enforce_freshness=False,
            **event,
        )

    consumer = SharedEventBus(
        source_service="health-consumer",
        provenance_verifier=verifier,
    )
    received = []
    consumer.subscribe_with_context(
        "health.observed",
        received.append,
        handler_id="tests:ed25519-roundtrip",
    )
    monkeypatch.setattr(
        consumer, "_ensure_handler_delivery",
        lambda *_args: {"id": "handler-receipt", "processed": False},
    )
    monkeypatch.setattr(
        consumer, "_claim_handler_delivery",
        lambda _receipt: ("handler-receipt", "handler-claim"),
    )

    class _Heartbeat:
        def join(self, timeout=None):
            return None

    monkeypatch.setattr(
        consumer, "_start_handler_lease_heartbeat",
        lambda *_args: (threading.Event(), _Heartbeat()),
    )
    monkeypatch.setattr(
        consumer, "_mark_handler_delivery_processed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        consumer, "_mark_delivery_processed",
        lambda *_args, **_kwargs: True,
    )

    event = dict(db.record)
    event["source_instance"] = "another-process"
    consumer._dispatch_event(
        event,
        delivery_id="delivery-1",
        claim_token="group-claim",
    )

    assert len(received) == 1
    proof = received[0]["provenance"]
    assert proof is not None
    assert proof.producer.producer_id == "health-producer"
    assert proof.workspace_id == "workspace-a"
    assert proof.brand_id == "brand-a"
    assert proof.verification == "ed25519_v1"
    # The envelope has expired as a freshness token, but remains authentic.
    assert event["provenance_envelope"]["expires_at"] < 5_000
