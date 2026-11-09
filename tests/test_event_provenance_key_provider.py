import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.event_provenance import ProducerPrincipal, VerifiedEventProvenance
from core.durable_event_provenance import DurableProvenanceError
from core.event_provenance_key_provider import (
    DurableProvenanceKeyProvider,
    ProvenanceKeyConfigError,
)


KEY_ID_OLD = "KDO-TENANT-HEALTH-2026-01"
KEY_ID_NEW = "KDO-TENANT-HEALTH-2026-02"
PRODUCER_ID = "kdo-tenant-health-facebook"
PRODUCER_CLASS = "tenant_connector_health"
EVENT = {
    "event_id": "event-123",
    "event_name": "connector.health.observed",
    "workspace_id": "workspace-a",
    "brand_id": "brand-a",
    "payload": {"connection_id": "connection-a", "health_state": "healthy"},
}


def keypair(seed):
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(seed).decode(), base64.b64encode(public).decode(), public


def env_for(*, key_id=KEY_ID_NEW, seed=b"new-health-signing-seed-32-bytes", old=None):
    private_b64, public_b64, public = keypair(seed)
    public_config = {key_id: public_b64}
    owners = {key_id: PRODUCER_ID}
    if old:
        old_id, old_seed = old
        _, old_public_b64, _ = keypair(old_seed)
        public_config[old_id] = old_public_b64
        owners[old_id] = PRODUCER_ID
    return {
        "KDO_EVENT_PROVENANCE_ACTIVE_SIGNING_KEY_ID": key_id,
        "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_ID": PRODUCER_ID,
        "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_CLASS": PRODUCER_CLASS,
        "KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64": private_b64,
        "KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON": json.dumps(public_config),
        "KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON": json.dumps(owners),
        "KDO_EVENT_PROVENANCE_REVOKED_KEY_IDS_JSON": "[]",
    }, public


def proof(*, producer_id=PRODUCER_ID, producer_class=PRODUCER_CLASS):
    return VerifiedEventProvenance(
        producer=ProducerPrincipal(producer_id, producer_class),
        actor=None,
        workspace_id=EVENT["workspace_id"],
        brand_id=EVENT["brand_id"],
        verification="composition_root_capability",
    )


def sign(provider, *, event=EVENT, provenance=None):
    return provider.sign(
        provenance or proof(),
        event_id=event["event_id"],
        event_name=event["event_name"],
        payload=event["payload"],
        brand_id=event["brand_id"],
    )


def verify(provider, envelope, *, event=EVENT):
    return provider.verify(
        envelope,
        event_id=event["event_id"],
        event_name=event["event_name"],
        workspace_id=event["workspace_id"],
        brand_id=event["brand_id"],
        payload=event["payload"],
    )


def test_environment_provider_signs_and_verifies_with_kdo_prefixed_key():
    env, _ = env_for()
    provider = DurableProvenanceKeyProvider.from_environment(env)

    envelope = sign(provider)
    verified = verify(provider, envelope)

    assert provider.can_sign is True
    assert provider.active_key_id == KEY_ID_NEW
    assert verified.producer.producer_id == PRODUCER_ID
    assert verified.workspace_id == "workspace-a"
    assert verified.verification == "ed25519_v1"


def test_public_only_consumer_loads_no_private_key_and_can_verify():
    env, _ = env_for()
    consumer_env = {
        "KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON": env["KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON"],
        "KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON": env["KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON"],
    }
    producer = DurableProvenanceKeyProvider.from_environment(env)
    consumer = DurableProvenanceKeyProvider.from_environment(consumer_env)

    envelope = sign(producer)

    assert consumer.can_sign is False
    assert consumer.active_key_id is None
    assert verify(consumer, envelope).producer.producer_id == PRODUCER_ID
    assert "private" not in repr(consumer).lower()


def test_private_seed_is_never_in_provider_repr_or_public_config():
    env, _ = env_for()
    provider = DurableProvenanceKeyProvider.from_environment(env)

    assert env["KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64"] not in repr(provider)
    public_keys, owners = provider.public_verification_config()
    assert set(public_keys) == {KEY_ID_NEW}
    assert owners[KEY_ID_NEW] == PRODUCER_ID
    assert all(len(key) == 32 for key in public_keys.values())


def test_signer_rejects_a_different_producer_identity():
    env, _ = env_for()
    provider = DurableProvenanceKeyProvider.from_environment(env)

    with pytest.raises(DurableProvenanceError, match="signing_key_not_owned_by_verified_producer"):
        sign(provider, provenance=proof(producer_id="other-producer"))


def test_signer_rejects_brand_scope_substitution():
    env, _ = env_for()
    provider = DurableProvenanceKeyProvider.from_environment(env)
    wrong_brand = {**EVENT, "brand_id": "brand-b"}

    with pytest.raises(DurableProvenanceError, match="signing_brand_scope_mismatch"):
        sign(provider, event=wrong_brand)


def test_incomplete_signing_configuration_fails_closed():
    env, _ = env_for()
    env.pop("KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64")

    with pytest.raises(ProvenanceKeyConfigError, match="active_signing_configuration_incomplete"):
        DurableProvenanceKeyProvider.from_environment(env)


def test_wrong_private_public_pair_is_rejected():
    env, _ = env_for()
    other_private, _, _ = keypair(b"other-health-signing-seed-32byte")
    env["KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64"] = other_private

    with pytest.raises(ProvenanceKeyConfigError, match="active_private_public_key_mismatch"):
        DurableProvenanceKeyProvider.from_environment(env)


def test_rotation_keeps_old_public_key_while_new_key_becomes_active():
    old_seed = b"old-health-signing-seed-32-bytes"
    new_seed = b"new-health-signing-seed-32-bytes"
    old_env, _ = env_for(key_id=KEY_ID_OLD, seed=old_seed)
    old_provider = DurableProvenanceKeyProvider.from_environment(old_env)
    old_envelope = sign(old_provider)

    rotated_env, _ = env_for(
        key_id=KEY_ID_NEW,
        seed=new_seed,
        old=(KEY_ID_OLD, old_seed),
    )
    rotated = DurableProvenanceKeyProvider.from_environment(rotated_env)
    new_envelope = sign(rotated)

    assert verify(rotated, old_envelope).producer.producer_id == PRODUCER_ID
    assert verify(rotated, new_envelope).producer.producer_id == PRODUCER_ID
    assert rotated.active_key_id == KEY_ID_NEW


def test_revoked_key_is_not_accepted_for_verification():
    env, _ = env_for()
    producer = DurableProvenanceKeyProvider.from_environment(env)
    envelope = sign(producer)
    consumer_env = {
        "KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON": env["KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON"],
        "KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON": env["KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON"],
        "KDO_EVENT_PROVENANCE_REVOKED_KEY_IDS_JSON": json.dumps([KEY_ID_NEW]),
    }
    consumer = DurableProvenanceKeyProvider.from_environment(consumer_env)

    with pytest.raises(DurableProvenanceError, match="unknown or retired signing key"):
        verify(consumer, envelope)


def test_active_signing_key_cannot_be_revoked():
    env, _ = env_for()
    env["KDO_EVENT_PROVENANCE_REVOKED_KEY_IDS_JSON"] = json.dumps([KEY_ID_NEW])

    with pytest.raises(ProvenanceKeyConfigError, match="active_signing_key_is_revoked"):
        DurableProvenanceKeyProvider.from_environment(env)


def test_key_ids_must_use_kdo_prefix():
    env, _ = env_for(key_id="HEALTH-KEY-01")

    with pytest.raises(ProvenanceKeyConfigError, match="key_id_must_use_kdo_prefix"):
        DurableProvenanceKeyProvider.from_environment(env)


def test_owner_mapping_must_match_key_set():
    env, _ = env_for()
    env["KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON"] = "{}"

    with pytest.raises(ProvenanceKeyConfigError, match="public_key_owner_sets_must_match"):
        DurableProvenanceKeyProvider.from_environment(env)
