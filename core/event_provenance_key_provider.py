"""Environment-backed Ed25519 key custody for durable event provenance.

Only the trusted producer process receives a private seed. Consumers receive
public verification keys and key-owner metadata only. This module loads
configuration but never generates keys, writes secrets, or logs key material.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.durable_event_provenance import (
    DurableProvenanceError,
    sign_durable_provenance,
    verify_durable_provenance,
)
from core.event_provenance import VerifiedEventProvenance


class ProvenanceKeyConfigError(ValueError):
    """Raised when provenance key configuration is incomplete or unsafe."""


_KEY_ID_PATTERN = re.compile(r"^KDO-[A-Z0-9][A-Z0-9_-]{0,122}$")

_ENV_ACTIVE_KEY_ID = "KDO_EVENT_PROVENANCE_ACTIVE_SIGNING_KEY_ID"
_ENV_SIGNING_PRODUCER_ID = "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_ID"
_ENV_SIGNING_PRODUCER_CLASS = "KDO_EVENT_PROVENANCE_SIGNING_PRODUCER_CLASS"
_ENV_PRIVATE_SEED = "KDO_EVENT_PROVENANCE_SIGNING_PRIVATE_KEY_B64"
_ENV_PUBLIC_KEYS = "KDO_EVENT_PROVENANCE_PUBLIC_KEYS_JSON"
_ENV_KEY_OWNERS = "KDO_EVENT_PROVENANCE_KEY_OWNERS_JSON"
_ENV_REVOKED_KEYS = "KDO_EVENT_PROVENANCE_REVOKED_KEY_IDS_JSON"
_ENV_KEY_STATES = "KDO_EVENT_PROVENANCE_KEY_STATES_JSON"
_VALID_KEY_STATES = frozenset({"active", "retained", "revoked"})


def _decode_key(value: Any, *, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ProvenanceKeyConfigError(f"{label}_missing")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProvenanceKeyConfigError(f"{label}_invalid_base64") from exc
    if len(decoded) != 32:
        raise ProvenanceKeyConfigError(f"{label}_must_be_32_bytes")
    return decoded


def _json_object(raw: str | None, *, label: str) -> dict[str, Any]:
    if raw is None or not raw.strip():
        raise ProvenanceKeyConfigError(f"{label}_missing")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ProvenanceKeyConfigError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise ProvenanceKeyConfigError(f"{label}_must_be_object")
    return value


@dataclass(frozen=True)
class _ActiveSigningKey:
    key_id: str
    producer_id: str
    producer_class: str
    private_seed: bytes = field(repr=False)


class DurableProvenanceKeyProvider:
    """A single producer's active signer plus a public-only verification keyring.

    Keep one instance scoped to one producer runtime. Do not pass its signing
    methods or private seed to request handlers or unrelated components.
    """

    def __init__(
        self,
        *,
        public_keys: Mapping[str, bytes],
        key_owners: Mapping[str, str],
        active_signing_key: _ActiveSigningKey | None = None,
        revoked_key_ids: tuple[str, ...] | list[str] | set[str] = (),
        key_states: Mapping[str, str] | None = None,
    ) -> None:
        if any(not isinstance(item, str) for item in revoked_key_ids):
            raise ProvenanceKeyConfigError("revoked_key_ids_must_be_strings")
        revoked = frozenset(revoked_key_ids)
        supplied_states = key_states is not None
        states = dict(key_states or {})
        normalized_keys: dict[str, bytes] = {}
        normalized_owners: dict[str, str] = {}

        if set(public_keys) != set(key_owners):
            raise ProvenanceKeyConfigError("public_key_owner_sets_must_match")
        if not public_keys:
            raise ProvenanceKeyConfigError("at_least_one_public_key_required")
        if not revoked.issubset(set(public_keys)):
            raise ProvenanceKeyConfigError("revoked_key_id_not_in_public_keyring")
        if supplied_states:
            if set(states) != set(public_keys):
                raise ProvenanceKeyConfigError("key_state_key_set_must_match_public_keyring")
            if any(not isinstance(state, str) or state not in _VALID_KEY_STATES for state in states.values()):
                raise ProvenanceKeyConfigError("key_state_must_be_active_retained_or_revoked")
            state_revoked = {key_id for key_id, state in states.items() if state == "revoked"}
            if state_revoked != set(revoked):
                raise ProvenanceKeyConfigError("revoked_key_states_must_match_revocation_list")
            if sum(state == "active" for state in states.values()) > 1:
                raise ProvenanceKeyConfigError("only_one_active_signing_key_allowed")
        if any(not _KEY_ID_PATTERN.fullmatch(key_id) for key_id in revoked):
            raise ProvenanceKeyConfigError("revoked_key_id_must_use_kdo_prefix")
        for raw_key_id, raw_key in public_keys.items():
            key_id = str(raw_key_id)
            raw_owner = key_owners[raw_key_id]
            if not isinstance(raw_owner, str):
                raise ProvenanceKeyConfigError("key_owner_must_be_string")
            owner = raw_owner.strip()
            if not _KEY_ID_PATTERN.fullmatch(key_id):
                raise ProvenanceKeyConfigError("key_id_must_use_kdo_prefix")
            if not owner:
                raise ProvenanceKeyConfigError("key_owner_missing")
            if not isinstance(raw_key, bytes) or len(raw_key) != 32:
                raise ProvenanceKeyConfigError("public_key_must_be_32_bytes")
            if key_id not in revoked:
                normalized_keys[key_id] = bytes(raw_key)
                normalized_owners[key_id] = owner

        if active_signing_key is not None:
            active = active_signing_key
            if supplied_states and states.get(active.key_id) != "active":
                raise ProvenanceKeyConfigError("active_signing_key_state_must_be_active")
            if not _KEY_ID_PATTERN.fullmatch(active.key_id):
                raise ProvenanceKeyConfigError("active_key_id_must_use_kdo_prefix")
            if active.key_id in revoked:
                raise ProvenanceKeyConfigError("active_signing_key_is_revoked")
            if not isinstance(active.private_seed, bytes) or len(active.private_seed) != 32:
                raise ProvenanceKeyConfigError("private_seed_must_be_32_bytes")
            if normalized_owners.get(active.key_id) != active.producer_id:
                raise ProvenanceKeyConfigError("active_key_owner_mismatch")
            derived_public = Ed25519PrivateKey.from_private_bytes(
                active.private_seed
            ).public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            if normalized_keys.get(active.key_id) != derived_public:
                raise ProvenanceKeyConfigError("active_private_public_key_mismatch")
            if not active.producer_id.strip() or not active.producer_class.strip():
                raise ProvenanceKeyConfigError("active_producer_identity_missing")

        self._active_signing_key = active_signing_key
        self._public_keys = MappingProxyType(normalized_keys)
        self._key_owners = MappingProxyType(normalized_owners)
        self._revoked_key_ids = revoked
        # Legacy deployments without explicit lifecycle metadata remain
        # compatible; do not guess whether consumer-only keys are active or
        # retained. Expose lifecycle states only when operators configure them.
        self._key_states = MappingProxyType(dict(states)) if supplied_states else MappingProxyType({})

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DurableProvenanceKeyProvider":
        """Load KDO-prefixed variables from an injected mapping or process env."""
        source = os.environ if environ is None else environ
        public_raw = source.get(_ENV_PUBLIC_KEYS)
        owners_raw = source.get(_ENV_KEY_OWNERS)
        public_config = _json_object(public_raw, label="public_keys")
        owner_config = _json_object(owners_raw, label="key_owners")

        public_keys = {
            str(key_id): _decode_key(value, label="public_key")
            for key_id, value in public_config.items()
        }
        key_owners = {
            str(key_id): owner
            for key_id, owner in owner_config.items()
        }

        revoked_raw = source.get(_ENV_REVOKED_KEYS, "[]")
        try:
            revoked_value = json.loads(revoked_raw)
        except (TypeError, ValueError) as exc:
            raise ProvenanceKeyConfigError("revoked_key_ids_invalid_json") from exc
        if not isinstance(revoked_value, list) or any(
            not isinstance(item, str) for item in revoked_value
        ):
            raise ProvenanceKeyConfigError("revoked_key_ids_must_be_string_array")

        states_raw = source.get(_ENV_KEY_STATES)
        states_config = _json_object(states_raw, label="key_states") if states_raw is not None else None

        signing_names = (
            _ENV_ACTIVE_KEY_ID,
            _ENV_SIGNING_PRODUCER_ID,
            _ENV_SIGNING_PRODUCER_CLASS,
            _ENV_PRIVATE_SEED,
        )
        signing_values = [source.get(name) for name in signing_names]
        active_key = None
        if any(value is not None for value in signing_values):
            if any(not isinstance(value, str) or not value.strip() for value in signing_values):
                raise ProvenanceKeyConfigError("active_signing_configuration_incomplete")
            active_key = _ActiveSigningKey(
                key_id=str(signing_values[0]).strip(),
                producer_id=str(signing_values[1]).strip(),
                producer_class=str(signing_values[2]).strip(),
                private_seed=_decode_key(signing_values[3], label="private_seed"),
            )

        return cls(
            public_keys=public_keys,
            key_owners=key_owners,
            active_signing_key=active_key,
            revoked_key_ids=revoked_value,
            key_states=states_config,
        )

    @property
    def can_sign(self) -> bool:
        return self._active_signing_key is not None

    @property
    def active_key_id(self) -> str | None:
        return self._active_signing_key.key_id if self._active_signing_key else None

    def sign(
        self,
        provenance: VerifiedEventProvenance,
        *,
        event_id: str,
        event_name: str,
        payload: Mapping[str, Any],
        brand_id: str | None,
    ) -> dict[str, Any]:
        active = self._active_signing_key
        if active is None:
            raise DurableProvenanceError("producer_signing_key_not_configured")
        if (
            not isinstance(provenance, VerifiedEventProvenance)
            or provenance.producer.producer_id != active.producer_id
            or provenance.producer.producer_class != active.producer_class
        ):
            raise DurableProvenanceError("signing_key_not_owned_by_verified_producer")
        if provenance.brand_id != brand_id:
            raise DurableProvenanceError("signing_brand_scope_mismatch")
        return sign_durable_provenance(
            provenance,
            event_id=event_id,
            event_name=event_name,
            payload=payload,
            key_id=active.key_id,
            signing_key=active.private_seed,
        )

    def verify(self, envelope: Mapping[str, Any], **event: Any):
        """Verify current operational evidence using public keys only."""
        return verify_durable_provenance(
            envelope,
            keyring=self._public_keys,
            key_owners=self._key_owners,
            enforce_freshness=True,
            **event,
        )

    @property
    def key_states(self) -> Mapping[str, str]:
        """Read-only lifecycle state for every configured public key ID."""
        return self._key_states

    def public_verification_config(self) -> tuple[dict[str, bytes], dict[str, str]]:
        """Return defensive copies containing public keys and owner labels only."""
        return dict(self._public_keys), dict(self._key_owners)

    def __repr__(self) -> str:
        return (
            "DurableProvenanceKeyProvider("
            f"active_key_id={self.active_key_id!r}, "
            f"verification_key_count={len(self._public_keys)}, "
            f"revoked_key_count={len(self._revoked_key_ids)}, "
            f"can_sign={self.can_sign})"
        )


__all__ = [
    "ProvenanceKeyConfigError",
    "DurableProvenanceKeyProvider",
]
