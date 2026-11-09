"""Server-side event producer provenance primitives for U2.

A source string is descriptive metadata, never proof of publisher identity.
Capabilities are minted by the composition root and injected only into the
internal component they identify. Serialized copies are data, not capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ProvenanceError(ValueError):
    """Raised when producer or actor provenance is invalid."""


@dataclass(frozen=True)
class ActorPrincipal:
    """Authenticated requester identity; distinct from the event producer."""

    principal_type: str
    subject_id: str | None
    auth_method: str
    workspace_id: str | None

    @classmethod
    def from_auth_identity(cls, identity: Any) -> "ActorPrincipal":
        if not getattr(identity, "valid", False):
            raise ProvenanceError("actor identity must be authenticated")
        method = str(getattr(identity, "method", "") or "").strip()
        if not method:
            raise ProvenanceError("authenticated identity has no method")
        user_id = getattr(identity, "user_id", None)
        is_species = bool(getattr(identity, "is_species", False))
        if user_id:
            principal_type = "human"
            subject_id = str(user_id)
        elif is_species:
            principal_type = "service_credential"
            subject_id = None
        else:
            # Workspace API credentials currently have no stable public
            # principal ID in AuthIdentity. Do not invent one from email,
            # workspace, or a secret token.
            principal_type = "credential"
            subject_id = None
        workspace_id = getattr(identity, "workspace_id", None)
        return cls(
            principal_type=principal_type,
            subject_id=subject_id,
            auth_method=method,
            workspace_id=str(workspace_id) if workspace_id else None,
        )


@dataclass(frozen=True)
class ProducerPrincipal:
    """Stable identity for a server-side component registered at boot."""

    producer_id: str
    producer_class: str


class ProducerCapability:
    """Opaque in-process publisher capability.

    Instances are minted by ProducerRegistry. Do not serialize or expose them
    through request state, event payloads, or public APIs.
    """

    __slots__ = ("_issuer", "principal")

    def __init__(self, issuer: object, principal: ProducerPrincipal) -> None:
        self._issuer = issuer
        self.principal = principal


@dataclass(frozen=True)
class VerifiedEventProvenance:
    """Verified in-process provenance; never trust a deserialized instance."""

    producer: ProducerPrincipal
    actor: ActorPrincipal | None
    workspace_id: str
    brand_id: str | None = None
    verification: str = "composition_root_capability"


def current_actor_principal() -> ActorPrincipal | None:
    """Return actor provenance only from the validated request auth context."""
    try:
        from api.support import auth_identity_var
        identity = auth_identity_var.get()
    except Exception:
        return None
    if identity is None:
        return None
    return ActorPrincipal.from_auth_identity(identity)


class ProducerRegistry:
    """Registry owned by the application composition root.

    Build once during trusted startup, issue each component its own capability,
    and inject only that capability into the component's publisher adapter.
    Runtime services must not receive this registry or an issuance method.
    """

    def __init__(self, producers: Mapping[str, str]) -> None:
        normalized: dict[str, ProducerPrincipal] = {}
        for producer_id, producer_class in producers.items():
            pid = str(producer_id).strip()
            pclass = str(producer_class).strip()
            if not pid or not pclass or pid in normalized:
                raise ProvenanceError("producer registration is invalid")
            normalized[pid] = ProducerPrincipal(pid, pclass)
        self._issuer = object()
        self._principals = normalized

    def issue(self, producer_id: str) -> ProducerCapability:
        """Mint a capability during trusted bootstrap only."""
        principal = self._principals.get(str(producer_id))
        if principal is None:
            raise ProvenanceError("producer is not registered")
        return ProducerCapability(self._issuer, principal)

    def verify(
        self,
        capability: ProducerCapability,
        *,
        workspace_id: str,
        actor: ActorPrincipal | None = None,
        brand_id: str | None = None,
    ) -> VerifiedEventProvenance:
        """Verify object provenance and tenant binding before event publish."""
        if not isinstance(capability, ProducerCapability):
            raise ProvenanceError("publisher capability is required")
        if capability._issuer is not self._issuer:
            raise ProvenanceError("publisher capability was not issued by this registry")
        registered = self._principals.get(capability.principal.producer_id)
        if registered is None or registered != capability.principal:
            raise ProvenanceError("publisher capability is not registered")
        workspace = str(workspace_id or "").strip()
        if not workspace:
            raise ProvenanceError("workspace binding is required")
        if actor is not None and actor.workspace_id and actor.workspace_id != workspace:
            raise ProvenanceError("actor and event workspace do not match")
        return VerifiedEventProvenance(
            producer=registered,
            actor=actor,
            workspace_id=workspace,
            brand_id=str(brand_id) if brand_id else None,
        )


__all__ = [
    "ActorPrincipal",
    "ProducerPrincipal",
    "ProducerCapability",
    "ProducerRegistry",
    "VerifiedEventProvenance",
    "ProvenanceError",
    "current_actor_principal",
]
