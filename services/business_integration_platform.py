"""V69 business integration + onboarding contracts.

Declarative control-plane contracts only. This module does not authenticate,
store secrets, call providers, grant authority, or execute external actions.
Those effects remain owned by the existing security, Decision Gate, Action
Kernel, outbox, execution and reconciliation planes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import uuid
from typing import Any, Mapping, Sequence


class ConnectionState(str, Enum):
    DISCOVERED = "discovered"
    AUTH_REQUIRED = "auth_required"
    AUTHENTICATED = "authenticated"
    VALIDATING = "validating"
    CAPABILITIES_DISCOVERED = "capabilities_discovered"
    MAPPING_REQUIRED = "mapping_required"
    CERTIFICATION_REQUIRED = "certification_required"
    OBSERVE_ONLY = "observe_only"
    SUGGEST_ENABLED = "suggest_enabled"
    EXECUTION_ENABLED = "execution_enabled"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class AutonomyMode(str, Enum):
    OBSERVE = "observe"
    SUGGEST = "suggest"
    EXECUTE = "execute"


@dataclass(frozen=True)
class BusinessOperatingIdentity:
    workspace_id: str
    name: str
    industry: str = ""
    description: str = ""
    objectives: tuple[str, ...] = ()
    operating_hours: Mapping[str, Any] = field(default_factory=dict)
    policies: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    identity_id: str = field(default_factory=lambda: f"biz_{uuid.uuid4().hex[:16]}")

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.name.strip():
            raise ValueError("business name is required")
        if len(set(self.objectives)) != len(self.objectives):
            raise ValueError("objectives must be unique")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "industry": self.industry,
            "description": self.description,
            "objectives": list(self.objectives),
            "operating_hours": _canonicalize(self.operating_hours),
            "policies": _canonicalize(self.policies),
            "metadata": _canonicalize(self.metadata),
            "identity_id": self.identity_id,
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ConnectorContract:
    connector_key: str
    provider: str
    version: str
    capabilities: tuple[str, ...] = ()
    inbound_events: tuple[str, ...] = ()
    outbound_actions: tuple[str, ...] = ()
    authentication_method: str = "oauth2"
    risk_by_action: Mapping[str, str] = field(default_factory=dict)
    requires_reconciliation: bool = True
    tenant_scoped: bool = True
    certified: bool = False

    def __post_init__(self) -> None:
        if not self.connector_key.strip() or not self.provider.strip():
            raise ValueError("connector_key and provider are required")
        if not self.version.strip():
            raise ValueError("connector version is required")
        if not self.tenant_scoped:
            raise ValueError("connectors must declare tenant_scoped=True")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        if any(r not in {"low", "medium", "high", "critical"} for r in self.risk_by_action.values()):
            raise ValueError("invalid connector action risk")

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class IntegrationConnection:
    workspace_id: str
    connector_key: str
    state: ConnectionState = ConnectionState.DISCOVERED
    granted_capabilities: tuple[str, ...] = ()
    autonomy_mode: AutonomyMode = AutonomyMode.OBSERVE
    connection_id: str = field(default_factory=lambda: f"int_{uuid.uuid4().hex[:16]}")

    # Secrets/credentials are deliberately absent from this contract.
    authority: str = field(default="none", init=False)
    execution_effect: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.connector_key.strip():
            raise ValueError("workspace_id and connector_key are required")
        if len(set(self.granted_capabilities)) != len(self.granted_capabilities):
            raise ValueError("granted_capabilities must be unique")
        if self.authority != "none" or self.execution_effect != "none":
            raise ValueError("integration connection cannot grant authority")
        if self.autonomy_mode == AutonomyMode.EXECUTE and self.state != ConnectionState.EXECUTION_ENABLED:
            raise ValueError("execute mode requires execution_enabled state")

    def has_capability(self, capability: str) -> bool:
        return capability in self.granted_capabilities


def certify_connection_transition(
    connection: IntegrationConnection,
    target: ConnectionState,
    *,
    connector: ConnectorContract,
    required_capabilities: Sequence[str] = (),
    mapping_valid: bool = False,
    health_ok: bool = False,
    certification_ok: bool = False,
) -> IntegrationConnection:
    """Pure state transition guard; never authenticates or executes."""
    if connection.workspace_id == "" or connector.connector_key != connection.connector_key:
        raise ValueError("workspace/connector identity mismatch")
    if not connector.tenant_scoped:
        raise ValueError("connector is not tenant scoped")
    if target == ConnectionState.CAPABILITIES_DISCOVERED and not all(connector.supports(c) for c in required_capabilities):
        raise ValueError("required capability is not declared by connector")
    if target == ConnectionState.MAPPING_REQUIRED and not required_capabilities:
        raise ValueError("mapping transition requires declared capabilities")
    if target == ConnectionState.CERTIFICATION_REQUIRED and not health_ok:
        raise ValueError("healthy connection required before certification")
    if target == ConnectionState.OBSERVE_ONLY and not (mapping_valid and certification_ok):
        raise ValueError("observe-only requires valid mapping and certification")
    if target == ConnectionState.SUGGEST_ENABLED and connection.state != ConnectionState.OBSERVE_ONLY:
        raise ValueError("suggest mode requires observe-only state")
    if target == ConnectionState.EXECUTION_ENABLED:
        if connection.state != ConnectionState.SUGGEST_ENABLED:
            raise ValueError("execution requires suggest-enabled state")
        if not connector.certified or not certification_ok:
            raise ValueError("execution requires certified connector")
    return IntegrationConnection(
        workspace_id=connection.workspace_id,
        connector_key=connection.connector_key,
        state=target,
        granted_capabilities=connection.granted_capabilities,
        autonomy_mode=(AutonomyMode.EXECUTE if target == ConnectionState.EXECUTION_ENABLED
                        else AutonomyMode.SUGGEST if target == ConnectionState.SUGGEST_ENABLED
                        else AutonomyMode.OBSERVE),
        connection_id=connection.connection_id,
    )


def validate_objective_capabilities(
    workspace_id: str,
    required_capabilities: Sequence[str],
    connections: Sequence[IntegrationConnection],
) -> dict[str, Any]:
    """Map an objective to currently granted, same-tenant capabilities."""
    if not workspace_id.strip():
        raise ValueError("workspace_id is required")
    tenant_connections = [c for c in connections if c.workspace_id == workspace_id]
    available = sorted({cap for c in tenant_connections for cap in c.granted_capabilities
                        if c.state in {ConnectionState.OBSERVE_ONLY, ConnectionState.SUGGEST_ENABLED,
                                       ConnectionState.EXECUTION_ENABLED}})
    required = sorted(set(str(c) for c in required_capabilities))
    missing = sorted(set(required) - set(available))
    return {"workspace_id": workspace_id, "required": required, "available": available,
            "missing": missing, "ready": not missing}


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(value[k]) for k in sorted(value, key=lambda x: str(x))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value
