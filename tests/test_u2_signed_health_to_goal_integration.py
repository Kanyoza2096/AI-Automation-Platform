from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api.support import auth_identity_var
from core.event_provenance import ProducerRegistry
from core.organism import OrganismRegistry
from core.unified_bus import UnifiedEventBus
from organism.coordinator import cycle_started_organisms
from organism.event_bridge import OrganismEventBridge
from organism.perception import ObservationKind


PRODUCER_ID = "kdo-tenant-health-facebook"
PRODUCER_CLASS = "tenant_connector_health"


def health_payload(*, evidence_id="evidence-001", evidence_hash="a" * 64):
    return {
        "service": "facebook:connection-001",
        "status": "degraded",
        "health_state": "degraded",
        "connector_key": "facebook",
        "provider": "facebook",
        "connector_version": "1.0.0",
        "connection_id": "connection-001",
        "social_account_id": "page-account-001",
        "observation_id": "observation-001",
        "evidence_id": evidence_id,
        "evidence_hash": evidence_hash,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


class IdempotentProposalStore:
    def __init__(self):
        self.items = {}

    async def persist(self, proposal):
        if proposal.proposal_id in self.items:
            return 0
        self.items[proposal.proposal_id] = proposal
        return 1

    async def list_pending(self, workspace_id):
        return tuple(
            proposal for proposal in self.items.values()
            if proposal.workspace_id == workspace_id
        )


class RegistryView:
    def __init__(self, organism):
        self.organism = organism

    def get(self, workspace_id):
        return self.organism if workspace_id == self.organism.workspace_id else None

    def list_ids(self):
        return [self.organism.workspace_id]


@pytest.mark.asyncio
async def test_verified_tenant_health_event_flows_through_bus_bridge_and_suggest_only_goal():
    registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
    capability = registry.issue(PRODUCER_ID)
    bus = UnifiedEventBus(producer_registry=registry)

    organisms = OrganismRegistry()
    organism = organisms.get_or_create("workspace-a")
    organism._running = True
    runtime = organism.runtime
    store = IdempotentProposalStore()
    runtime.goal_proposal_store = store

    # Goal proposal processing must not call a decision plane or execute work.
    class NoDecisionPlane:
        async def evaluate(self, **_kwargs):
            raise AssertionError("suggest-only goal derivation must not invoke decisions")

    runtime.cognitive = NoDecisionPlane()

    bridge = OrganismEventBridge(organisms)
    await bridge.start(bus)

    identity = SimpleNamespace(
        valid=True,
        method="api_key",
        user_id="user-a",
        is_species=False,
        workspace_id="workspace-a",
    )
    token = auth_identity_var.set(identity)
    try:
        event_id = await bus.publish(
            "connector.health.observed",
            health_payload(),
            source="kdo.tenant_facebook_health",
            workspace_id="workspace-a",
            brand_id="brand-a",
            producer_capability=capability,
        )
        assert event_id
        await bus._dispatch_queue.join()

        observations = runtime.perception.recent(
            "workspace-a", kind=ObservationKind.HEALTH
        )
        assert len(observations) == 1
        observation = observations[0]
        assert observation.source_trust == 0.9
        assert observation.confidence == 0.9
        assert observation.metadata["producer_id"] == PRODUCER_ID
        assert observation.metadata["brand_id"] == "brand-a"
        assert observation.metadata["evidence_id"] == "evidence-001"
        assert observation.metadata["social_account_id"] == "page-account-001"

        result = await cycle_started_organisms(organisms)

        assert result.attempted == 1
        assert result.completed == 1
        assert result.failed == 0
        assert len(store.items) == 1
        proposal = next(iter(store.items.values()))
        assert proposal.workspace_id == "workspace-a"
        assert proposal.source_event_ids == (event_id,)
        assert proposal.authority == "suggest"
        assert proposal.status == "proposed"
        assert runtime.pending_goal_proposals == (proposal,)
    finally:
        auth_identity_var.reset(token)
        await bridge.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_unsigned_connector_health_event_cannot_create_a_goal_proposal():
    bus = UnifiedEventBus()
    organisms = OrganismRegistry()
    organism = organisms.get_or_create("workspace-a")
    organism._running = True
    runtime = organism.runtime
    store = IdempotentProposalStore()
    runtime.goal_proposal_store = store

    bridge = OrganismEventBridge(organisms)
    await bridge.start(bus)
    try:
        event_id = await bus.publish(
            "connector.health.observed",
            health_payload(evidence_id="evidence-unsigned"),
            source="kdo.tenant_facebook_health",
            workspace_id="workspace-a",
            brand_id="brand-a",
        )
        assert event_id
        await bus._dispatch_queue.join()

        observations = runtime.perception.recent(
            "workspace-a", kind=ObservationKind.HEALTH
        )
        assert len(observations) == 1
        assert observations[0].source_trust == 0.0
        assert observations[0].metadata["producer_id"] is None

        result = await cycle_started_organisms(organisms)

        assert result.completed == 1
        assert result.failed == 0
        assert store.items == {}
        assert runtime.pending_goal_proposals == ()
    finally:
        await bridge.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_republished_health_evidence_deduplicates_goal_by_evidence_id():
    producer_registry = ProducerRegistry({PRODUCER_ID: PRODUCER_CLASS})
    capability = producer_registry.issue(PRODUCER_ID)
    bus = UnifiedEventBus(producer_registry=producer_registry)
    organisms = OrganismRegistry()
    organism = organisms.get_or_create("workspace-a")
    organism._running = True
    runtime = organism.runtime
    store = IdempotentProposalStore()
    runtime.goal_proposal_store = store
    bridge = OrganismEventBridge(organisms)
    await bridge.start(bus)

    identity = SimpleNamespace(
        valid=True,
        method="api_key",
        user_id="user-a",
        is_species=False,
        workspace_id="workspace-a",
    )
    token = auth_identity_var.set(identity)
    try:
        first_id = await bus.publish(
            "connector.health.observed",
            health_payload(evidence_id="same-durable-evidence"),
            source="kdo.tenant_facebook_health",
            workspace_id="workspace-a",
            brand_id="brand-a",
            producer_capability=capability,
        )
        await bus._dispatch_queue.join()
        first = await cycle_started_organisms(organisms)

        second_id = await bus.publish(
            "connector.health.observed",
            health_payload(evidence_id="same-durable-evidence"),
            source="kdo.tenant_facebook_health",
            workspace_id="workspace-a",
            brand_id="brand-a",
            producer_capability=capability,
        )
        await bus._dispatch_queue.join()
        second = await cycle_started_organisms(organisms)

        assert first_id and second_id and first_id != second_id
        assert first.completed == second.completed == 1
        assert len(store.items) == 1
        proposal = next(iter(store.items.values()))
        assert proposal.source_event_ids == (first_id,)
        assert proposal.authority == "suggest"
        assert proposal.status == "proposed"
    finally:
        auth_identity_var.reset(token)
        await bridge.stop()
        await bus.stop()



@pytest.mark.asyncio
async def test_verified_durable_health_replay_flows_to_suggest_only_goal():
    import asyncio
    import threading
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.durable_event_provenance import (
        sign_durable_provenance,
        verify_durable_provenance,
    )
    from core.event_provenance import (
        ActorPrincipal,
        ProducerPrincipal,
        VerifiedEventProvenance,
    )
    from core.shared_event_bus import SharedEventBus

    signing_seed = b"kdo-durable-replay-test-key-32!!"[:32]
    private_key = Ed25519PrivateKey.from_private_bytes(signing_seed)
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    event_id = "durable-health-replay-001"
    payload = health_payload(evidence_id="durable-evidence-001")
    proof = VerifiedEventProvenance(
        producer=ProducerPrincipal(PRODUCER_ID, PRODUCER_CLASS),
        actor=ActorPrincipal(
            principal_type="human",
            subject_id="user-a",
            auth_method="api_key",
            workspace_id="workspace-a",
        ),
        workspace_id="workspace-a",
        brand_id="brand-a",
        verification="composition_root_capability",
    )
    envelope = sign_durable_provenance(
        proof,
        event_id=event_id,
        event_name="connector.health.observed",
        payload=payload,
        key_id="health-producer-key-v1",
        signing_key=signing_seed,
        issued_at=int(datetime.now(timezone.utc).timestamp()),
        ttl_seconds=3600,
    )

    def verifier(signed_envelope, **event):
        return verify_durable_provenance(
            signed_envelope,
            keyring={"health-producer-key-v1": public_key},
            key_owners={"health-producer-key-v1": PRODUCER_ID},
            enforce_freshness=False,
            **event,
        )

    consumer = SharedEventBus(
        source_service="organism-replay-consumer",
        provenance_verifier=verifier,
    )
    organisms = OrganismRegistry()
    organism = organisms.get_or_create("workspace-a")
    organism._running = True
    runtime = organism.runtime
    store = IdempotentProposalStore()
    runtime.goal_proposal_store = store

    class NoDecisionPlane:
        async def evaluate(self, **_kwargs):
            raise AssertionError("durable replay must remain suggest-only")

    runtime.cognitive = NoDecisionPlane()
    bridge = OrganismEventBridge(organisms)
    await bridge.start_shared(consumer)

    class Heartbeat:
        def join(self, timeout=None):
            return None

    consumer._ensure_handler_delivery = lambda *_args: {
        "id": "handler-receipt-001",
        "processed": False,
    }
    consumer._claim_handler_delivery = lambda _receipt: (
        "handler-receipt-001",
        "handler-claim-001",
    )
    consumer._start_handler_lease_heartbeat = lambda *_args: (
        threading.Event(),
        Heartbeat(),
    )
    consumer._mark_handler_delivery_processed = lambda *_args, **_kwargs: True
    consumer._mark_delivery_processed = lambda *_args, **_kwargs: True

    event = {
        "id": event_id,
        "event_type": "connector.health.observed",
        "workspace_id": "workspace-a",
        "brand_id": "brand-a",
        "source_service": "untrusted-legacy-label",
        "source_instance": "remote-health-worker",
        "payload": payload,
        "provenance_envelope": envelope,
    }
    try:
        # SharedEventBus runs async context handlers in its dispatch thread.
        await asyncio.to_thread(
            consumer._dispatch_event,
            event,
            delivery_id="group-delivery-001",
            claim_token="group-claim-001",
        )

        observations = runtime.perception.recent(
            "workspace-a", kind=ObservationKind.HEALTH
        )
        assert len(observations) == 1
        assert observations[0].source_trust == 0.9
        assert observations[0].metadata["provenance_verification"] == "ed25519_v1"
        assert observations[0].metadata["evidence_id"] == "durable-evidence-001"

        result = await cycle_started_organisms(organisms)

        assert result.attempted == result.completed == 1
        assert result.failed == 0
        assert len(store.items) == 1
        proposal = next(iter(store.items.values()))
        assert proposal.workspace_id == "workspace-a"
        assert proposal.source_event_ids == (event_id,)
        assert proposal.authority == "suggest"
        assert proposal.status == "proposed"
        assert runtime.pending_goal_proposals == (proposal,)
    finally:
        await bridge.stop()
        consumer.stop()
