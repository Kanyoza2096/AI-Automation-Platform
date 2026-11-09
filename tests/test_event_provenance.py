from dataclasses import dataclass

import pytest

from core.event_provenance import (
    ActorPrincipal,
    ProducerRegistry,
    ProvenanceError,
    VerifiedEventProvenance,
    current_actor_principal,
)


@dataclass
class FakeAuthIdentity:
    valid: bool = True
    method: str = "supabase_jwt"
    user_id: str | None = "user-123"
    workspace_id: str | None = "workspace-a"
    is_species: bool = False


def test_actor_principal_uses_authenticated_subject_not_request_metadata():
    actor = ActorPrincipal.from_auth_identity(FakeAuthIdentity())
    assert actor.principal_type == "human"
    assert actor.subject_id == "user-123"
    assert actor.auth_method == "supabase_jwt"
    assert actor.workspace_id == "workspace-a"


def test_actor_principal_does_not_invent_subject_for_service_credential():
    identity = FakeAuthIdentity(
        method="python_service",
        user_id=None,
        workspace_id=None,
        is_species=True,
    )
    actor = ActorPrincipal.from_auth_identity(identity)
    assert actor.principal_type == "service_credential"
    assert actor.subject_id is None


def test_actor_principal_rejects_unauthenticated_identity():
    with pytest.raises(ProvenanceError, match="authenticated"):
        ActorPrincipal.from_auth_identity(FakeAuthIdentity(valid=False))


def test_registry_mints_and_verifies_registered_publisher_capability():
    registry = ProducerRegistry({"health-monitor": "internal_service"})
    capability = registry.issue("health-monitor")
    actor = ActorPrincipal.from_auth_identity(FakeAuthIdentity())
    provenance = registry.verify(
        capability,
        workspace_id="workspace-a",
        actor=actor,
        brand_id="brand-a",
    )
    assert isinstance(provenance, VerifiedEventProvenance)
    assert provenance.producer.producer_id == "health-monitor"
    assert provenance.producer.producer_class == "internal_service"
    assert provenance.actor == actor
    assert provenance.workspace_id == "workspace-a"
    assert provenance.brand_id == "brand-a"


def test_unknown_producer_cannot_receive_capability():
    registry = ProducerRegistry({"health-monitor": "internal_service"})
    with pytest.raises(ProvenanceError, match="not registered"):
        registry.issue("caller-supplied-source")


def test_capability_from_another_registry_is_rejected():
    first = ProducerRegistry({"health-monitor": "internal_service"})
    second = ProducerRegistry({"health-monitor": "internal_service"})
    capability = first.issue("health-monitor")
    with pytest.raises(ProvenanceError, match="not issued"):
        second.verify(capability, workspace_id="workspace-a")


def test_caller_supplied_string_or_serialized_metadata_is_not_capability():
    registry = ProducerRegistry({"health-monitor": "internal_service"})
    for forged in (
        "health-monitor",
        {"producer_id": "health-monitor", "verified": True},
        {"source_trust": 1.0, "producer": "health-monitor"},
    ):
        with pytest.raises(ProvenanceError, match="capability"):
            registry.verify(forged, workspace_id="workspace-a")


def test_missing_workspace_is_rejected():
    registry = ProducerRegistry({"health-monitor": "internal_service"})
    with pytest.raises(ProvenanceError, match="workspace"):
        registry.verify(registry.issue("health-monitor"), workspace_id="")


def test_actor_workspace_mismatch_is_rejected():
    registry = ProducerRegistry({"health-monitor": "internal_service"})
    actor = ActorPrincipal.from_auth_identity(FakeAuthIdentity())
    with pytest.raises(ProvenanceError, match="do not match"):
        registry.verify(
            registry.issue("health-monitor"),
            workspace_id="workspace-b",
            actor=actor,
        )


def test_duplicate_or_invalid_producer_registration_is_rejected():
    with pytest.raises(ProvenanceError, match="registration"):
        ProducerRegistry({"": "internal_service"})
    with pytest.raises(ProvenanceError, match="registration"):
        ProducerRegistry({"health-monitor": ""})


def test_current_actor_principal_reads_only_validated_auth_context(monkeypatch):
    from api.support import auth_identity_var

    assert current_actor_principal() is None
    identity = FakeAuthIdentity()
    token = auth_identity_var.set(identity)
    try:
        actor = current_actor_principal()
        assert actor is not None
        assert actor.subject_id == "user-123"
        assert actor.workspace_id == "workspace-a"
    finally:
        auth_identity_var.reset(token)
    assert current_actor_principal() is None


def test_current_actor_principal_rejects_invalid_identity_in_context():
    from api.support import auth_identity_var

    token = auth_identity_var.set(FakeAuthIdentity(valid=False))
    try:
        with pytest.raises(ProvenanceError, match="authenticated"):
            current_actor_principal()
    finally:
        auth_identity_var.reset(token)
