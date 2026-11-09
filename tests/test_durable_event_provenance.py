import copy

import pytest

from core.durable_event_provenance import (
    DurableProvenanceError,
    sign_durable_provenance,
    verify_durable_provenance,
)
from core.event_provenance import ActorPrincipal, ProducerPrincipal, VerifiedEventProvenance


KEY_A = b"a" * 32
KEY_B = b"b" * 32
EVENT = {
    "event_id": "evt-123",
    "event_name": "engine_alert",
    "workspace_id": "workspace-a",
    "brand_id": "brand-a",
    "payload": {"service": "api", "status": "degraded"},
}
PROOF = VerifiedEventProvenance(
    producer=ProducerPrincipal("health-monitor", "internal_health_producer"),
    actor=ActorPrincipal("human", "user-1", "supabase_jwt", "workspace-a"),
    workspace_id="workspace-a",
    brand_id="brand-a",
)


def sign(key_id="key-v1", key=KEY_A, **overrides):
    args = {**EVENT, **overrides}
    return sign_durable_provenance(
        PROOF,
        event_id=args["event_id"],
        event_name=args["event_name"],
        payload=args["payload"],
        key_id=key_id,
        signing_key=key,
        issued_at=1_000,
        ttl_seconds=300,
    )


def verify(envelope, *, keyring=None, now=1_100, **overrides):
    args = {**EVENT, **overrides}
    return verify_durable_provenance(
        envelope,
        keyring=keyring or {"key-v1": KEY_A},
        event_id=args["event_id"],
        event_name=args["event_name"],
        workspace_id=args["workspace_id"],
        payload=args["payload"],
        brand_id=args["brand_id"],
        now=now,
    )


def test_signed_envelope_round_trips_to_verified_provenance():
    envelope = sign()
    result = verify(envelope)
    assert result.producer.producer_id == "health-monitor"
    assert result.actor.subject_id == "user-1"
    assert result.workspace_id == "workspace-a"
    assert result.verification == "hmac_sha256_v1"


@pytest.mark.parametrize("field,value", [
    ("event_id", "evt-other"),
    ("event_name", "dream.insight"),
    ("workspace_id", "workspace-b"),
    ("brand_id", "brand-b"),
])
def test_signature_cannot_be_replayed_for_different_event_binding(field, value):
    envelope = sign()
    with pytest.raises(DurableProvenanceError):
        verify(envelope, **{field: value})


def test_payload_mutation_invalidates_binding():
    envelope = sign()
    changed = {"service": "api", "status": "healthy"}
    with pytest.raises(DurableProvenanceError):
        verify(envelope, payload=changed)


def test_envelope_tampering_invalidates_signature():
    envelope = sign()
    envelope["producer"]["producer_id"] = "attacker"
    with pytest.raises(DurableProvenanceError):
        verify(envelope)


def test_unknown_or_retired_key_is_rejected():
    envelope = sign()
    with pytest.raises(DurableProvenanceError, match="unknown or retired"):
        verify(envelope, keyring={"key-v2": KEY_B})


def test_key_rotation_accepts_retained_old_key_and_new_key():
    old_envelope = sign("key-v1", KEY_A)
    new_envelope = sign("key-v2", KEY_B)
    keyring = {"key-v1": KEY_A, "key-v2": KEY_B}
    assert verify(old_envelope, keyring=keyring).producer.producer_id == "health-monitor"
    assert verify(new_envelope, keyring=keyring).producer.producer_id == "health-monitor"


def test_expired_and_future_envelopes_are_rejected():
    envelope = sign()
    with pytest.raises(DurableProvenanceError, match="expired"):
        verify(envelope, now=1_300)
    with pytest.raises(DurableProvenanceError, match="future"):
        verify(envelope, now=900)


def test_serialized_verified_flag_does_not_replace_signature():
    envelope = sign()
    envelope.pop("signature")
    envelope["verified"] = True
    with pytest.raises(DurableProvenanceError):
        verify(envelope)


def test_signing_requires_live_provenance_and_strong_key():
    with pytest.raises(DurableProvenanceError):
        sign_durable_provenance(
            {"verification": "composition_root_capability"},
            event_id="evt",
            event_name="engine_alert",
            payload={},
            key_id="key-v1",
            signing_key=KEY_A,
            issued_at=1_000,
        )
    with pytest.raises(DurableProvenanceError):
        sign_durable_provenance(
            PROOF,
            event_id="evt",
            event_name="engine_alert",
            payload={},
            key_id="key-v1",
            signing_key=b"short",
            issued_at=1_000,
        )


def test_actor_workspace_mismatch_rejected_even_with_valid_signature():
    envelope = sign()
    # Re-signing a mismatched actor is not exposed through normal event flow;
    # even a correctly signed envelope must preserve the actor/event binding.
    from core.durable_event_provenance import _signed_fields, _canonical_json
    import hashlib
    import hmac

    fields = {k: v for k, v in envelope.items() if k != "signature"}
    fields["actor"]["workspace_id"] = "workspace-b"
    envelope = {
        **fields,
        "signature": hmac.new(KEY_A, _canonical_json(fields), hashlib.sha256).hexdigest(),
    }
    with pytest.raises(DurableProvenanceError, match="actor workspace"):
        verify(envelope)
