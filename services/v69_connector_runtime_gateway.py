"""V69.10 — runtime connector gateway and capability enforcement.

This is the runtime choke point between KDO governance and an external connector.
Certification/activation evidence is consumed here, but never treated as authority.
Mutating calls require an ACTIVE registry record, an EXECUTION_ENABLED connection,
an EXECUTE autonomy mode, a granted+declared capability, version alignment, and a
real GovernedConnector adapter. The gateway never calls a raw connector mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import uuid4

from services.v69_integration_evidence import EvidenceSink, emit

from services.business_integration_platform import AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection
from services.connectors.governed_connector import GovernedConnector
from services.v69_connector_activation_registry import ConnectorRegistryRecord, RegistryFailure, RegistryState


class RuntimeBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    CONNECTOR_MISMATCH = "connector_mismatch"
    REGISTRY_NOT_ACTIVE = "registry_not_active"
    CERTIFICATION_MISSING = "certification_missing"
    CERTIFICATION_EXPIRED = "certification_expired"
    VERSION_DRIFT = "version_drift"
    CONNECTION_NOT_EXECUTION_ENABLED = "connection_not_execution_enabled"
    AUTONOMY_NOT_EXECUTE = "autonomy_not_execute"
    CAPABILITY_NOT_DECLARED = "capability_not_declared"
    CAPABILITY_NOT_GRANTED = "capability_not_granted"
    ACTION_NOT_DECLARED = "action_not_declared"
    GOVERNED_ADAPTER_REQUIRED = "governed_adapter_required"
    PROVIDER_MISMATCH = "provider_mismatch"


@dataclass(frozen=True)
class RuntimeAuthorization:
    allowed: bool
    workspace_id: str
    connection_id: str
    connector_key: str
    action: str
    capability: str
    reasons: tuple[str, ...] = ()
    authority_granted: bool = False


class ConnectorRuntimeBlocked(PermissionError):
    """Fail-closed runtime block; no provider operation was attempted."""

    def __init__(self, authorization: RuntimeAuthorization):
        self.authorization = authorization
        super().__init__("connector runtime blocked: " + ",".join(authorization.reasons))


@dataclass(frozen=True)
class RuntimeConnectorGateway:
    """Immutable, tenant-bound facade for connector runtime operations."""

    workspace_id: str
    connection: IntegrationConnection
    contract: ConnectorContract
    registry: ConnectorRegistryRecord
    governed: GovernedConnector
    evidence_sink: EvidenceSink | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if self.connection.workspace_id != self.workspace_id:
            raise ValueError(RuntimeBlock.TENANT_MISMATCH.value)
        if self.registry.workspace_id != self.workspace_id:
            raise ValueError(RuntimeBlock.TENANT_MISMATCH.value)
        if self.connection.connector_key != self.contract.connector_key or self.registry.connector_key != self.contract.connector_key:
            raise ValueError(RuntimeBlock.CONNECTOR_MISMATCH.value)
        if not isinstance(self.governed, GovernedConnector):
            raise ValueError(RuntimeBlock.GOVERNED_ADAPTER_REQUIRED.value)
        if self.governed.workspace_id != self.workspace_id:
            raise ValueError(RuntimeBlock.TENANT_MISMATCH.value)

    def authorize(self, action: str, *, capability: str | None = None, now=None) -> RuntimeAuthorization:
        capability = capability or action
        reasons: list[str] = []
        if self.connection.workspace_id != self.workspace_id or self.registry.workspace_id != self.workspace_id:
            reasons.append(RuntimeBlock.TENANT_MISMATCH.value)
        if self.connection.connection_id != self.registry.connection_id:
            reasons.append(RuntimeBlock.TENANT_MISMATCH.value)
        if self.connection.connector_key != self.contract.connector_key or self.registry.connector_key != self.contract.connector_key:
            reasons.append(RuntimeBlock.CONNECTOR_MISMATCH.value)
        if self.registry.state != RegistryState.ACTIVE:
            reasons.append(RuntimeBlock.REGISTRY_NOT_ACTIVE.value)
        if self.registry.evidence is None:
            reasons.append(RuntimeBlock.CERTIFICATION_MISSING.value)
        elif self.registry.evidence.is_expired(now):
            reasons.append(RuntimeBlock.CERTIFICATION_EXPIRED.value)
        elif self.registry.evidence.connector_version != self.contract.version:
            reasons.append(RuntimeBlock.VERSION_DRIFT.value)
        if self.connection.state != ConnectionState.EXECUTION_ENABLED:
            reasons.append(RuntimeBlock.CONNECTION_NOT_EXECUTION_ENABLED.value)
        if self.connection.autonomy_mode != AutonomyMode.EXECUTE:
            reasons.append(RuntimeBlock.AUTONOMY_NOT_EXECUTE.value)
        if not self.contract.supports(capability):
            reasons.append(RuntimeBlock.CAPABILITY_NOT_DECLARED.value)
        if not self.connection.has_capability(capability):
            reasons.append(RuntimeBlock.CAPABILITY_NOT_GRANTED.value)
        if action not in self.contract.outbound_actions:
            reasons.append(RuntimeBlock.ACTION_NOT_DECLARED.value)
        if self.governed.platform not in {self.contract.provider, self.contract.connector_key}:
            reasons.append(RuntimeBlock.PROVIDER_MISMATCH.value)
        authorization = RuntimeAuthorization(
            allowed=not reasons,
            workspace_id=self.workspace_id,
            connection_id=self.connection.connection_id,
            connector_key=self.contract.connector_key,
            action=action,
            capability=capability,
            reasons=tuple(dict.fromkeys(reasons)),
            authority_granted=False,
        )
        if self.evidence_sink is not None:
            emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id,
                 connection_id=self.connection.connection_id, connector_key=self.contract.connector_key,
                 event_type="runtime_allow" if authorization.allowed else "runtime_block",
                 outcome="allowed" if authorization.allowed else "blocked", action=action,
                 reason_codes=authorization.reasons, metadata={"capability": capability})
        return authorization

    def _require(self, action: str, *, capability: str | None = None, now=None) -> RuntimeAuthorization:
        auth = self.authorize(action, capability=capability, now=now)
        if not auth.allowed:
            raise ConnectorRuntimeBlocked(auth)
        return auth

    async def publish(self, content: str, *, idempotency_key: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        self._require("publish", capability="publish")
        return await self.governed.publish(content, idempotency_key=idempotency_key, **kwargs)

    async def edit(self, post_id: str, content: str, *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        self._require("edit", capability="edit")
        return await self.governed.edit(post_id, content, idempotency_key=idempotency_key)

    async def delete(self, post_id: str, *, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        self._require("delete", capability="delete")
        return await self.governed.delete(post_id, idempotency_key=idempotency_key)

    # Read-only surfaces are deliberately not execution-authorized, but remain
    # tenant-bound and are exposed only through this facade rather than raw adapters.
    def lookup_post(self, post_id: str) -> Any:
        return self.governed.lookup_post(post_id)

    def health(self) -> Any:
        return self.governed.health()

    def observe_health(self, evidence_store: Any, *, observed_at=None) -> Any:
        """Probe this tenant-bound connector and atomically persist its observation.

        This is a read-only provider check plus an audit write. It does not
        authorize connector mutations, publish an event, or grant authority.
        A durable store must be supplied explicitly; there is no memory or
        database-client fallback.
        """
        recorder = getattr(evidence_store, "record_health_observation", None)
        if not callable(recorder):
            raise ValueError("durable_health_observation_store_required")

        result = self.governed.health()
        if isinstance(result, Mapping):
            ok = result.get("ok")
            platform = result.get("platform")
            latency_value = result.get("latency_ms")
            status_value = result.get("status_code", result.get("provider_status_code"))
        else:
            ok = getattr(result, "ok", None)
            platform = getattr(result, "platform", None)
            latency_value = getattr(result, "latency_ms", None)
            status_value = getattr(result, "status_code", getattr(result, "provider_status_code", None))

        if not isinstance(ok, bool):
            raise ValueError("provider_health_result_invalid")
        if platform and str(platform) not in {self.contract.provider, self.contract.connector_key}:
            raise ValueError(RuntimeBlock.PROVIDER_MISMATCH.value)

        try:
            latency_ms = None if latency_value is None else max(0, int(round(float(latency_value))))
            provider_status_code = None if status_value is None else int(status_value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("provider_health_metrics_invalid") from exc

        health_state = "healthy" if ok else "unknown"
        reason_codes = () if ok else ("provider_check_not_healthy",)
        metadata = {
            "provider_check": "success" if ok else "not_healthy",
            "latency_ms": latency_ms,
        }
        return recorder(
            workspace_id=self.workspace_id,
            connection_id=self.connection.connection_id,
            connector_key=self.contract.connector_key,
            health_state=health_state,
            observed_at=observed_at,
            drift=(),
            provider_status_code=provider_status_code,
            latency_ms=latency_ms,
            reason_codes=reason_codes,
            actor="system",
            provider=self.contract.provider,
            connector_version=self.contract.version,
            metadata=metadata,
        )

    def validate(self) -> Any:
        return self.governed.validate()


__all__ = ["RuntimeBlock", "RuntimeAuthorization", "ConnectorRuntimeBlocked", "RuntimeConnectorGateway"]
