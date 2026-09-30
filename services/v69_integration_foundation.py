"""V69.3-V69.5 integration foundation.

Control-plane only: opaque credential references, capability discovery, and
canonical event/data mapping. No secrets, provider calls, approvals, or side
 effects are performed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence

from services.business_integration_platform import ConnectorContract, ConnectionState, IntegrationConnection


class CredentialKind(str, Enum):
    OAUTH = "oauth"
    API_KEY = "api_key"
    SERVICE_ACCOUNT = "service_account"
    CUSTOM = "custom"


@dataclass(frozen=True)
class CredentialBinding:
    workspace_id: str
    connection_id: str
    kind: CredentialKind
    secret_ref: str
    external_subject: str = ""
    fingerprint: str = ""
    active: bool = True

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connection_id.strip():
            raise ValueError("workspace_id and connection_id are required")
        if not self.secret_ref.strip():
            raise ValueError("opaque secret_ref is required")
        if self.secret_ref.startswith(("sk_", "eyJ", "Bearer ")):
            raise ValueError("raw credential material is forbidden")
        if self.fingerprint and len(self.fingerprint) != 64:
            raise ValueError("credential fingerprint must be sha256")


@dataclass(frozen=True)
class ConnectionSecurityStatus:
    workspace_id: str
    connection_id: str
    authenticated: bool
    credential_bound: bool
    credential_active: bool
    external_subject_known: bool
    tenant_verified: bool
    revocation_required: bool = False

    @property
    def safe_for_discovery(self) -> bool:
        return self.authenticated and self.credential_bound and self.credential_active and self.tenant_verified


def validate_credential_binding(connection: IntegrationConnection, binding: CredentialBinding) -> ConnectionSecurityStatus:
    if connection.workspace_id != binding.workspace_id or connection.connection_id != binding.connection_id:
        raise ValueError("credential/connection tenant identity mismatch")
    if connection.state in {ConnectionState.REVOKED, ConnectionState.SUSPENDED}:
        raise ValueError("cannot bind credentials to revoked or suspended connection")
    authenticated = connection.state in {
        ConnectionState.AUTHENTICATED, ConnectionState.VALIDATING,
        ConnectionState.CAPABILITIES_DISCOVERED, ConnectionState.MAPPING_REQUIRED,
        ConnectionState.CERTIFICATION_REQUIRED, ConnectionState.OBSERVE_ONLY,
        ConnectionState.SUGGEST_ENABLED, ConnectionState.EXECUTION_ENABLED,
    }
    return ConnectionSecurityStatus(
        workspace_id=connection.workspace_id,
        connection_id=connection.connection_id,
        authenticated=authenticated,
        credential_bound=True,
        credential_active=binding.active,
        external_subject_known=bool(binding.external_subject),
        tenant_verified=True,
        revocation_required=not binding.active,
    )


def revoke_credential(binding: CredentialBinding) -> CredentialBinding:
    return CredentialBinding(
        workspace_id=binding.workspace_id,
        connection_id=binding.connection_id,
        kind=binding.kind,
        secret_ref=binding.secret_ref,
        external_subject=binding.external_subject,
        fingerprint=binding.fingerprint,
        active=False,
    )


@dataclass(frozen=True)
class CapabilityDiscovery:
    workspace_id: str
    connection_id: str
    connector_key: str
    declared_capabilities: tuple[str, ...]
    declared_events: tuple[str, ...]
    declared_actions: tuple[str, ...]
    discovered_capabilities: tuple[str, ...]
    unsupported_declared: tuple[str, ...] = ()
    tenant_verified: bool = True

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connection_id.strip():
            raise ValueError("tenant and connection identity are required")
        if not self.tenant_verified:
            raise ValueError("capability discovery requires tenant verification")
        if not set(self.discovered_capabilities).issubset(set(self.declared_capabilities)):
            raise ValueError("discovered capability cannot exceed connector declaration")

    @property
    def ready(self) -> bool:
        return not self.unsupported_declared and bool(self.discovered_capabilities)


def discover_capabilities(
    connection: IntegrationConnection,
    connector: ConnectorContract,
    *,
    observed_capabilities: Sequence[str] = (),
    observed_events: Sequence[str] = (),
    observed_actions: Sequence[str] = (),
    tenant_verified: bool = True,
) -> CapabilityDiscovery:
    if connection.connector_key != connector.connector_key:
        raise ValueError("connector identity mismatch")
    if connection.workspace_id.strip() == "":
        raise ValueError("workspace_id is required")
    declared_caps = tuple(sorted(set(connector.capabilities)))
    observed = tuple(sorted(set(observed_capabilities)))
    unsupported = tuple(sorted(set(observed) - set(declared_caps)))
    if unsupported:
        raise ValueError("provider exposed undeclared capability")
    if not set(observed_events).issubset(set(connector.inbound_events)):
        raise ValueError("provider exposed undeclared event")
    if not set(observed_actions).issubset(set(connector.outbound_actions)):
        raise ValueError("provider exposed undeclared action")
    return CapabilityDiscovery(
        workspace_id=connection.workspace_id,
        connection_id=connection.connection_id,
        connector_key=connector.connector_key,
        declared_capabilities=declared_caps,
        declared_events=tuple(sorted(connector.inbound_events)),
        declared_actions=tuple(sorted(connector.outbound_actions)),
        discovered_capabilities=observed,
        tenant_verified=tenant_verified,
    )


@dataclass(frozen=True)
class FieldMapping:
    canonical_field: str
    external_path: str
    required: bool = False

    def __post_init__(self) -> None:
        if not self.canonical_field.strip() or not self.external_path.strip():
            raise ValueError("mapping fields are required")
        if self.external_path.startswith(("http://", "https://")):
            raise ValueError("external_path must be a field path, not a URL")


@dataclass(frozen=True)
class EventMapping:
    workspace_id: str
    connection_id: str
    external_event: str
    canonical_event: str
    fields: tuple[FieldMapping, ...] = ()
    tenant_field: str | None = None
    stable_event_id_field: str | None = None
    version: str = "1"

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connection_id.strip():
            raise ValueError("workspace_id and connection_id are required")
        if not self.external_event.strip() or not self.canonical_event.strip():
            raise ValueError("event names are required")
        names = [f.canonical_field for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("canonical mapping fields must be unique")
        if self.tenant_field == self.stable_event_id_field and self.tenant_field:
            raise ValueError("tenant and stable event identity fields must be distinct")

    def canonical_payload(self, external_payload: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for mapping in self.fields:
            value = _path_get(external_payload, mapping.external_path)
            if value is None and mapping.required:
                raise ValueError(f"required mapped field missing:{mapping.canonical_field}")
            if value is not None:
                result[mapping.canonical_field] = value
        if self.stable_event_id_field:
            event_id = _path_get(external_payload, self.stable_event_id_field)
            if not event_id:
                raise ValueError("stable event id is required")
            result["event_id"] = str(event_id)
        return result

    def fingerprint(self) -> str:
        raw = json.dumps({
            "workspace_id": self.workspace_id,
            "connection_id": self.connection_id,
            "external_event": self.external_event,
            "canonical_event": self.canonical_event,
            "fields": [f.__dict__ for f in self.fields],
            "tenant_field": self.tenant_field,
            "stable_event_id_field": self.stable_event_id_field,
            "version": self.version,
        }, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()


def validate_event_mapping(
    mapping: EventMapping,
    connection: IntegrationConnection,
    connector: ConnectorContract,
) -> None:
    if mapping.workspace_id != connection.workspace_id:
        raise ValueError("mapping/connection workspace mismatch")
    if mapping.connection_id != connection.connection_id:
        raise ValueError("mapping/connection identity mismatch")
    if mapping.external_event not in connector.inbound_events:
        raise ValueError("event is not declared by connector")
    if connection.state not in {
        ConnectionState.MAPPING_REQUIRED, ConnectionState.CERTIFICATION_REQUIRED,
        ConnectionState.OBSERVE_ONLY, ConnectionState.SUGGEST_ENABLED,
        ConnectionState.EXECUTION_ENABLED,
    }:
        raise ValueError("connection is not ready for mapping validation")


def _path_get(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current
