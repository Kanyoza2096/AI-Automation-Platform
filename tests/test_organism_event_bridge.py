import pytest

from core.organism import OrganismRegistry
from organism.event_bridge import OrganismEventBridge
from organism.perception import ObservationKind


class _Bus:
    def __init__(self):
        self.handlers = []

    async def subscribe_all(self, handler):
        self.handlers.append(handler)

        def unsubscribe():
            self.handlers[:] = [item for item in self.handlers if item is not handler]

        return unsubscribe

    async def emit(self, event):
        for handler in tuple(self.handlers):
            await handler(event)


@pytest.mark.asyncio
async def test_bridge_ingests_only_allowlisted_fields_into_matching_workspace():
    registry = OrganismRegistry()
    organism = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    await bus.emit({
        "event_name": "service_heartbeat",
        "event_id": "evt-1",
        "workspace_id": "workspace-a",
        "source": "monitor",
        "correlation_id": "corr-1",
        "payload": {
            "service": "api",
            "status": "healthy",
            "cpu_percent": 22.5,
            "api_key": "must-not-enter-runtime",
            "error": "must-not-enter-runtime",
        },
    })

    observations = organism.runtime.perception.recent("workspace-a")
    assert len(observations) == 1
    observation = observations[0]
    assert observation.kind is ObservationKind.HEALTH
    assert observation.value == {
        "service": "api",
        "status": "healthy",
        "cpu_percent": 22.5,
    }
    assert observation.metadata["event_id"] == "evt-1"
    assert observation.confidence == 0.8
    # Source labels are not authenticated publisher identity; this event may
    # inform perception but must not qualify for goal proposal derivation.
    assert observation.source_trust == 0.0
    assert "api_key" not in observation.value
    assert "error" not in observation.value


@pytest.mark.asyncio
async def test_bridge_never_falls_back_to_default_or_creates_unknown_workspaces():
    registry = OrganismRegistry()
    existing = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    await bus.emit({
        "event_name": "task_failed",
        "event_id": "evt-unscoped",
        "payload": {"action": "publish", "status": "failed"},
    })
    await bus.emit({
        "event_name": "task_failed",
        "event_id": "evt-unknown-workspace",
        "workspace_id": "workspace-b",
        "payload": {"action": "publish", "status": "failed"},
    })

    assert registry.get("default") is None
    assert registry.get("workspace-b") is None
    assert existing.runtime.perception.recent("workspace-a") == ()


@pytest.mark.asyncio
async def test_bridge_deduplicates_events_and_ignores_unrecognized_types():
    registry = OrganismRegistry()
    organism = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()
    await bridge.start(bus)

    event = {
        "event_name": "task_failed",
        "event_id": "evt-duplicate",
        "workspace_id": "workspace-a",
        "payload": {"action": "publish", "status": "failed"},
    }
    await bus.emit(event)
    await bus.emit(event)
    await bus.emit({
        "event_name": "arbitrary.command",
        "event_id": "evt-not-allowlisted",
        "workspace_id": "workspace-a",
        "payload": {"action": "delete"},
    })

    observations = organism.runtime.perception.recent("workspace-a")
    assert len(observations) == 1
    assert observations[0].kind is ObservationKind.EXECUTION


@pytest.mark.asyncio
async def test_bridge_start_is_idempotent_and_stop_unsubscribes():
    registry = OrganismRegistry()
    organism = registry.get_or_create("workspace-a")
    bridge = OrganismEventBridge(registry)
    bus = _Bus()

    await bridge.start(bus)
    await bridge.start(bus)
    assert len(bus.handlers) == 1

    await bridge.stop()
    assert bus.handlers == []
    await bus.emit({
        "event_name": "service_heartbeat",
        "event_id": "evt-after-stop",
        "workspace_id": "workspace-a",
        "payload": {"status": "healthy"},
    })
    assert organism.runtime.perception.recent("workspace-a") == ()


from dataclasses import replace

from core.event_provenance import ActorPrincipal, ProducerPrincipal, VerifiedEventProvenance
from organism.event_bridge import observation_from_event


def _facebook_health_proof(
    *,
    producer_id="kdo-tenant-health-facebook",
    producer_class="tenant_connector_health",
    workspace_id="workspace-a",
    brand_id="brand-a",
    verification="composition_root_capability",
):
    return VerifiedEventProvenance(
        producer=ProducerPrincipal(producer_id, producer_class),
        actor=ActorPrincipal(
            principal_type="human",
            subject_id="user-a",
            auth_method="api_key",
            workspace_id=workspace_id,
        ),
        workspace_id=workspace_id,
        brand_id=brand_id,
        verification=verification,
    )


def _facebook_health_event(provenance=None, **payload_overrides):
    payload = {
        "service": "facebook:connection-a",
        "status": "degraded",
        "health_state": "degraded",
        "connection_id": "connection-a",
        "social_account_id": "account-a",
        "connector_key": "facebook",
        "provider": "facebook",
        "connector_version": "1.0.0",
        "observation_id": "observation-a",
        "evidence_id": "evidence-a",
        "evidence_hash": "a" * 64,
        "observed_at": "2026-11-09T07:00:00+00:00",
    }
    payload.update(payload_overrides)
    return {
        "event_name": "connector.health.observed",
        "event_id": "event-a",
        "workspace_id": "workspace-a",
        "brand_id": "brand-a",
        "source": "caller-controlled-source-label",
        "payload": payload,
        "provenance": provenance,
    }


def test_signed_facebook_health_observation_gets_trust_only_with_full_evidence_lineage():
    observation = observation_from_event(
        _facebook_health_event(_facebook_health_proof(verification="ed25519_v1"))
    )

    assert observation is not None
    assert observation.source == "unified_event:connector.health.observed"
    assert observation.source_trust == 0.9
    assert observation.confidence == 0.9
    assert observation.metadata["producer_id"] == "kdo-tenant-health-facebook"
    assert observation.metadata["evidence_id"] == "evidence-a"
    assert observation.metadata["evidence_hash"] == "a" * 64
    assert observation.value["health_state"] == "degraded"


@pytest.mark.parametrize("provenance", [
    None,
    _facebook_health_proof(producer_id="other-producer"),
    _facebook_health_proof(producer_class="platform_monitor"),
    _facebook_health_proof(workspace_id="workspace-b"),
    _facebook_health_proof(brand_id="brand-b"),
    _facebook_health_proof(verification="serialized_verified_flag"),
    _facebook_health_proof().__class__(
        producer=ProducerPrincipal("kdo-tenant-health-facebook", "tenant_connector_health"),
        actor=None,
        workspace_id="workspace-a",
        brand_id="brand-a",
        verification="ed25519_v1",
    ),
])
def test_facebook_health_source_labels_or_mismatched_proofs_never_raise_trust(provenance):
    observation = observation_from_event(_facebook_health_event(provenance))

    assert observation is not None
    assert observation.source_trust == 0.0
    assert observation.confidence == 0.8
    assert observation.metadata["producer_id"] is None


@pytest.mark.parametrize("overrides", [
    {"connector_key": "instagram"},
    {"provider": "instagram"},
    {"evidence_hash": "not-a-sha256"},
    {"evidence_id": ""},
    {"connection_id": ""},
    {"status": "degraded", "health_state": "healthy"},
    {"observed_at": "2026-11-09T07:00:00"},
])
def test_facebook_health_evidence_must_bind_account_and_observation_fields(overrides):
    observation = observation_from_event(
        _facebook_health_event(_facebook_health_proof(), **overrides)
    )

    assert observation is not None
    assert observation.source_trust == 0.0
