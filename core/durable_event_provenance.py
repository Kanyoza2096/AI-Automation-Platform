"""Signed durable event provenance envelopes (U2 prototype).

This module verifies signed provenance after serialization/replay. It does not
trust source labels, payload flags, or a serialized "verified" boolean. Keys
must be injected by a trusted composition root/key provider and must never be
stored alongside event rows.
"""
from __future__ import annotations

import hashlib
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
import json
import time
from typing import Any, Mapping

from core.event_provenance import (
    ActorPrincipal,
    ProducerPrincipal,
    VerifiedEventProvenance,
    ProvenanceError,
)

ENVELOPE_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"
MAX_ENVELOPE_BYTES = 16_384


class DurableProvenanceError(ProvenanceError):
    """Raised when a signed durable provenance envelope is invalid."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DurableProvenanceError("value is not canonical JSON") from exc


def _payload_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(payload))).hexdigest()


def _signed_fields(
    *,
    key_id: str,
    event_id: str,
    event_name: str,
    workspace_id: str,
    brand_id: str | None,
    payload_sha256: str,
    producer_id: str,
    producer_class: str,
    actor: dict[str, Any] | None,
    issued_at: int,
    expires_at: int,
) -> dict[str, Any]:
    return {
        "version": ENVELOPE_VERSION,
        "algorithm": SIGNATURE_ALGORITHM,
        "key_id": key_id,
        "event_id": event_id,
        "event_name": event_name,
        "workspace_id": workspace_id,
        "brand_id": brand_id,
        "payload_sha256": payload_sha256,
        "producer": {"producer_id": producer_id, "producer_class": producer_class},
        "actor": actor,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }


def sign_durable_provenance(
    provenance: VerifiedEventProvenance,
    *,
    event_id: str,
    event_name: str,
    payload: Mapping[str, Any],
    key_id: str,
    signing_key: bytes,
    issued_at: int | None = None,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    """Sign provenance already verified in-process; returns JSON-safe data."""
    if not isinstance(provenance, VerifiedEventProvenance) or provenance.verification != "composition_root_capability":
        raise DurableProvenanceError("live composition-root verified provenance is required")
    if not provenance.workspace_id or not provenance.producer.producer_id or not provenance.producer.producer_class:
        raise DurableProvenanceError("verified producer and workspace identity are required")
    if provenance.actor is not None and provenance.actor.workspace_id != provenance.workspace_id:
        raise DurableProvenanceError("actor and provenance workspace do not match")
    if not key_id or len(key_id) > 128:
        raise DurableProvenanceError("valid signing key id is required")
    if not isinstance(signing_key, bytes) or len(signing_key) != 32:
        raise DurableProvenanceError("Ed25519 private seed must contain exactly 32 bytes")
    if not event_id or not event_name or not isinstance(payload, Mapping):
        raise DurableProvenanceError("event identity and object payload are required")
    if not isinstance(ttl_seconds, int) or ttl_seconds < 1 or ttl_seconds > 86_400:
        raise DurableProvenanceError("ttl_seconds must be between 1 and 86400")
    now = int(time.time()) if issued_at is None else int(issued_at)
    actor = None
    if provenance.actor is not None:
        actor = {
            "principal_type": provenance.actor.principal_type,
            "subject_id": provenance.actor.subject_id,
            "auth_method": provenance.actor.auth_method,
            "workspace_id": provenance.actor.workspace_id,
        }
    fields = _signed_fields(
        key_id=key_id,
        event_id=str(event_id),
        event_name=str(event_name),
        workspace_id=provenance.workspace_id,
        brand_id=provenance.brand_id,
        payload_sha256=_payload_digest(payload),
        producer_id=provenance.producer.producer_id,
        producer_class=provenance.producer.producer_class,
        actor=actor,
        issued_at=now,
        expires_at=now + ttl_seconds,
    )
    try:
        private_key = Ed25519PrivateKey.from_private_bytes(signing_key)
        signature = private_key.sign(_canonical_json(fields)).hex()
    except Exception as exc:
        raise DurableProvenanceError("Ed25519 signing failed") from exc
    return {**fields, "signature": signature}


def verify_durable_provenance(
    envelope: Mapping[str, Any],
    *,
    keyring: Mapping[str, bytes],
    key_owners: Mapping[str, str],
    event_id: str,
    event_name: str,
    workspace_id: str,
    payload: Mapping[str, Any],
    brand_id: str | None = None,
    now: int | None = None,
    max_age_seconds: int = 86_400,
    clock_skew_seconds: int = 60,
) -> VerifiedEventProvenance:
    """Verify Ed25519 signature, freshness, event binding, and producer key ownership.

    Verifiers receive public keys only. During rotation, keyring may contain the
    active public key and retained previous public keys. Remove an old key only
    after all envelopes signed by it have expired (or emergency revocation).
    """
    if not isinstance(envelope, Mapping) or len(_canonical_json(dict(envelope))) > MAX_ENVELOPE_BYTES:
        raise DurableProvenanceError("invalid or oversized provenance envelope")
    signature = envelope.get("signature")
    key_id = envelope.get("key_id")
    if not isinstance(signature, str) or len(signature) != 128:
        raise DurableProvenanceError("missing or malformed Ed25519 signature")
    if not isinstance(key_id, str) or key_id not in keyring:
        raise DurableProvenanceError("unknown or retired signing key")
    key = keyring[key_id]
    if not isinstance(key, bytes) or len(key) != 32:
        raise DurableProvenanceError("Ed25519 public verification key is invalid")

    signed = {k: v for k, v in envelope.items() if k != "signature"}
    try:
        public_key = Ed25519PublicKey.from_public_bytes(key)
        public_key.verify(bytes.fromhex(signature), _canonical_json(signed))
    except (InvalidSignature, ValueError) as exc:
        raise DurableProvenanceError("provenance signature verification failed") from exc

    if envelope.get("version") != ENVELOPE_VERSION or envelope.get("algorithm") != SIGNATURE_ALGORITHM:
        raise DurableProvenanceError("unsupported provenance envelope version or algorithm")
    if (
        envelope.get("event_id") != str(event_id)
        or envelope.get("event_name") != str(event_name)
        or envelope.get("workspace_id") != str(workspace_id)
        or envelope.get("brand_id") != brand_id
        or envelope.get("payload_sha256") != _payload_digest(payload)
    ):
        raise DurableProvenanceError("provenance does not bind to this event")

    issued_at, expires_at = envelope.get("issued_at"), envelope.get("expires_at")
    current = int(time.time()) if now is None else int(now)
    if not isinstance(issued_at, int) or not isinstance(expires_at, int) or expires_at <= issued_at:
        raise DurableProvenanceError("invalid provenance validity interval")
    if issued_at > current + clock_skew_seconds:
        raise DurableProvenanceError("provenance issued in the future")
    if current >= expires_at or current - issued_at > max_age_seconds:
        raise DurableProvenanceError("provenance has expired")

    producer = envelope.get("producer")
    if not isinstance(producer, Mapping):
        raise DurableProvenanceError("producer identity is missing")
    producer_id, producer_class = producer.get("producer_id"), producer.get("producer_class")
    if not isinstance(producer_id, str) or not producer_id or not isinstance(producer_class, str) or not producer_class:
        raise DurableProvenanceError("producer identity is malformed")
    if key_owners.get(key_id) != producer_id:
        raise DurableProvenanceError("signing key is not authorized for this producer")

    actor_data = envelope.get("actor")
    actor = None
    if actor_data is not None:
        if not isinstance(actor_data, Mapping):
            raise DurableProvenanceError("actor identity is malformed")
        actor = ActorPrincipal(
            principal_type=str(actor_data.get("principal_type") or ""),
            subject_id=actor_data.get("subject_id"),
            auth_method=str(actor_data.get("auth_method") or ""),
            workspace_id=actor_data.get("workspace_id"),
        )
        if not actor.principal_type or not actor.auth_method or actor.workspace_id != workspace_id:
            raise DurableProvenanceError("actor workspace or identity is invalid")

    return VerifiedEventProvenance(
        producer=ProducerPrincipal(producer_id, producer_class),
        actor=actor,
        workspace_id=str(workspace_id),
        brand_id=brand_id,
        verification="ed25519_v1",
    )


__all__ = [
    "ENVELOPE_VERSION",
    "SIGNATURE_ALGORITHM",
    "DurableProvenanceError",
    "sign_durable_provenance",
    "verify_durable_provenance",
]
