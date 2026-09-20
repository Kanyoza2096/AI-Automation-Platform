"""V69.12 — unified governed integration lifecycle orchestrator.

This module composes the V69 integration control-plane contracts into one
fail-closed lifecycle. It does not authenticate, store secrets, grant
authority, or execute provider side effects. It only determines and applies
safe control-plane state transitions; Action Kernel and durable execution
remain authoritative for mutations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Mapping
from uuid import uuid4

from services.v69_integration_evidence import EvidenceSink, emit

from services.business_integration_platform import (
    AutonomyMode,
    ConnectionState,
    ConnectorContract,
    IntegrationConnection,
)
from services.v69_connector_activation_registry import (
    ConnectorRegistryRecord,
    RegistryState,
    evaluate_activation,
    transition_registry,
)
from services.v69_connector_health_control import (
    ControlAction,
    ControlDecision,
    HealthObservation,
    apply_control_decision,
    assess_health,
)


class LifecycleBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_TRANSITION = "invalid_transition"
    CERTIFICATION_REQUIRED = "certification_required"
    MAPPING_REQUIRED = "mapping_required"
    HEALTH_REQUIRED = "health_required"
    REGISTRY_REQUIRED = "registry_required"
    EXECUTION_NOT_AUTHORIZED = "execution_not_authorized"
    CONNECTION_REVOKED = "connection_revoked"


class LifecycleAction(str, Enum):
    DISCOVER = "discover"
    AUTHENTICATE = "authenticate"
    VALIDATE = "validate"
    DISCOVER_CAPABILITIES = "discover_capabilities"
    MAP = "map"
    CERTIFY = "certify"
    OBSERVE = "observe"
    SUGGEST = "suggest"
    EXECUTE = "execute"
    DEGRADE = "degrade"
    SUSPEND = "suspend"
    REVOKE = "revoke"
    RECERTIFY = "recertify"
    REACTIVATE = "reactivate"


@dataclass(frozen=True)
class LifecycleTransition:
    action: LifecycleAction
    from_state: ConnectionState
    to_state: ConnectionState
    allowed: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleSnapshot:
    workspace_id: str
    connection: IntegrationConnection
    registry: ConnectorRegistryRecord | None
    execution_eligible: bool
    inbound_eligible: bool
    reasons: tuple[str, ...] = ()

    @property
    def authority_granted(self) -> bool:
        return False


# This is intentionally explicit. No arbitrary jumps to execution are possible.
_ALLOWED: Mapping[ConnectionState, frozenset[ConnectionState]] = {
    ConnectionState.DISCOVERED: frozenset({ConnectionState.AUTH_REQUIRED}),
    ConnectionState.AUTH_REQUIRED: frozenset({ConnectionState.AUTHENTICATED, ConnectionState.REVOKED}),
    ConnectionState.AUTHENTICATED: frozenset({ConnectionState.VALIDATING, ConnectionState.REVOKED}),
    ConnectionState.VALIDATING: frozenset({ConnectionState.CAPABILITIES_DISCOVERED, ConnectionState.REVOKED}),
    ConnectionState.CAPABILITIES_DISCOVERED: frozenset({ConnectionState.MAPPING_REQUIRED, ConnectionState.REVOKED}),
    ConnectionState.MAPPING_REQUIRED: frozenset({ConnectionState.CERTIFICATION_REQUIRED, ConnectionState.REVOKED}),
    ConnectionState.CERTIFICATION_REQUIRED: frozenset({ConnectionState.OBSERVE_ONLY, ConnectionState.REVOKED}),
    ConnectionState.OBSERVE_ONLY: frozenset({ConnectionState.SUGGEST_ENABLED, ConnectionState.DEGRADED, ConnectionState.SUSPENDED, ConnectionState.REVOKED}),
    ConnectionState.SUGGEST_ENABLED: frozenset({ConnectionState.EXECUTION_ENABLED, ConnectionState.DEGRADED, ConnectionState.SUSPENDED, ConnectionState.REVOKED}),
    ConnectionState.EXECUTION_ENABLED: frozenset({ConnectionState.DEGRADED, ConnectionState.SUSPENDED, ConnectionState.REVOKED}),
    ConnectionState.DEGRADED: frozenset({ConnectionState.OBSERVE_ONLY, ConnectionState.SUSPENDED, ConnectionState.REVOKED}),
    ConnectionState.SUSPENDED: frozenset({ConnectionState.AUTH_REQUIRED, ConnectionState.REVOKED}),
    ConnectionState.REVOKED: frozenset(),
}


@dataclass(frozen=True)
class IntegrationLifecycleOrchestrator:
    workspace_id: str
    connection: IntegrationConnection
    contract: ConnectorContract
    registry: ConnectorRegistryRecord | None = None
    evidence_sink: EvidenceSink | None = None

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        if self.connection.workspace_id != self.workspace_id:
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        if self.connection.connector_key != self.contract.connector_key:
            raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
        if self.registry is not None:
            if self.registry.workspace_id != self.workspace_id or self.registry.connection_id != self.connection.connection_id:
                raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)
            if self.registry.connector_key != self.contract.connector_key:
                raise ValueError(LifecycleBlock.TENANT_MISMATCH.value)

    def plan(self, target: ConnectionState, *, certification_ok: bool = False,
             mapping_valid: bool = False, health_ok: bool = False,
             required_capabilities: Iterable[str] = ()) -> LifecycleTransition:
        reasons: list[str] = []
        if target not in _ALLOWED.get(self.connection.state, frozenset()):
            reasons.append(LifecycleBlock.INVALID_TRANSITION.value)
        if target == ConnectionState.CERTIFICATION_REQUIRED and not health_ok:
            reasons.append(LifecycleBlock.HEALTH_REQUIRED.value)
        if target == ConnectionState.OBSERVE_ONLY:
            if not mapping_valid:
                reasons.append(LifecycleBlock.MAPPING_REQUIRED.value)
            if not certification_ok:
                reasons.append(LifecycleBlock.CERTIFICATION_REQUIRED.value)
        if target == ConnectionState.EXECUTION_ENABLED:
            if not certification_ok or not self.contract.certified:
                reasons.append(LifecycleBlock.CERTIFICATION_REQUIRED.value)
            if self.connection.state != ConnectionState.SUGGEST_ENABLED:
                reasons.append(LifecycleBlock.INVALID_TRANSITION.value)
            if self.registry is None or not self.registry.activation_eligible:
                reasons.append(LifecycleBlock.REGISTRY_REQUIRED.value)
            missing = sorted(set(required_capabilities) - set(self.connection.granted_capabilities))
            if missing:
                reasons.append("missing_capabilities:" + ",".join(missing))
        if self.connection.state == ConnectionState.REVOKED and target != ConnectionState.REVOKED:
            reasons.append(LifecycleBlock.CONNECTION_REVOKED.value)
        return LifecycleTransition(
            action=_action_for_target(target), from_state=self.connection.state,
            to_state=target, allowed=not reasons, reasons=tuple(dict.fromkeys(reasons)),
        )

    def transition(self, target: ConnectionState, *, certification_ok: bool = False,
                   mapping_valid: bool = False, health_ok: bool = False,
                   required_capabilities: Iterable[str] = ()) -> IntegrationConnection:
        plan = self.plan(target, certification_ok=certification_ok, mapping_valid=mapping_valid,
                         health_ok=health_ok, required_capabilities=required_capabilities)
        if self.evidence_sink is not None:
            emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id,
                 connection_id=self.connection.connection_id, connector_key=self.contract.connector_key,
                 event_type="lifecycle_transition", outcome="allowed" if plan.allowed else "blocked",
                 action=plan.action.value, reason_codes=plan.reasons,
                 metadata={"from_state": plan.from_state.value, "to_state": plan.to_state.value})
        if not plan.allowed:
            raise ValueError("integration lifecycle blocked: " + ",".join(plan.reasons))
        grants = self.connection.granted_capabilities
        return IntegrationConnection(
            workspace_id=self.connection.workspace_id,
            connector_key=self.connection.connector_key,
            state=target,
            granted_capabilities=grants,
            autonomy_mode=(AutonomyMode.EXECUTE if target == ConnectionState.EXECUTION_ENABLED
                            else AutonomyMode.SUGGEST if target == ConnectionState.SUGGEST_ENABLED
                            else AutonomyMode.OBSERVE),
            connection_id=self.connection.connection_id,
        )

    def health_control(self, observation: HealthObservation, *, now: datetime | None = None) -> tuple[ControlDecision, IntegrationConnection, ConnectorRegistryRecord | None]:
        if self.registry is None:
            raise ValueError(LifecycleBlock.REGISTRY_REQUIRED.value)
        decision = assess_health(observation, self.connection, self.registry, now=now)
        if self.evidence_sink is not None:
            emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id,
                 connection_id=self.connection.connection_id, connector_key=self.contract.connector_key,
                 event_type="health_observation", outcome=decision.action.value,
                 reason_codes=decision.reasons, metadata={"health_state": observation.state.value, "drift": [d.value for d in observation.drift]})
        new_registry = apply_control_decision(self.registry, self.connection, decision, now=now)
        new_connection = self.connection
        if decision.action in {ControlAction.REVOKE, ControlAction.SUSPEND}:
            target = ConnectionState.REVOKED if decision.action == ControlAction.REVOKE else ConnectionState.SUSPENDED
            new_connection = IntegrationConnection(
                workspace_id=self.connection.workspace_id,
                connector_key=self.connection.connector_key,
                state=target,
                granted_capabilities=self.connection.granted_capabilities,
                autonomy_mode=AutonomyMode.OBSERVE,
                connection_id=self.connection.connection_id,
            )
        elif decision.action in {ControlAction.DEGRADE, ControlAction.REQUIRE_RECERTIFICATION}:
            new_connection = IntegrationConnection(
                workspace_id=self.connection.workspace_id,
                connector_key=self.connection.connector_key,
                state=ConnectionState.DEGRADED,
                granted_capabilities=self.connection.granted_capabilities,
                autonomy_mode=AutonomyMode.OBSERVE,
                connection_id=self.connection.connection_id,
            )
        return decision, new_connection, new_registry

    def snapshot(self, *, now: datetime | None = None) -> LifecycleSnapshot:
        reasons: list[str] = []
        outbound = False
        inbound = self.connection.state in {
            ConnectionState.MAPPING_REQUIRED, ConnectionState.CERTIFICATION_REQUIRED,
            ConnectionState.OBSERVE_ONLY, ConnectionState.SUGGEST_ENABLED,
            ConnectionState.EXECUTION_ENABLED,
        } and self.connection.state not in {ConnectionState.REVOKED, ConnectionState.SUSPENDED}
        if self.registry is None:
            reasons.append(LifecycleBlock.REGISTRY_REQUIRED.value)
        else:
            result = evaluate_activation(self.registry, self.connection, now=now)
            outbound = bool(result["eligible"])
            reasons.extend(str(r) for r in result["reasons"])
        return LifecycleSnapshot(self.workspace_id, self.connection, self.registry, outbound, inbound,
                                 tuple(dict.fromkeys(reasons)))

    def apply_health(self, observation: HealthObservation, *, now: datetime | None = None) -> "IntegrationLifecycleOrchestrator":
        _, connection, registry = self.health_control(observation, now=now)
        return IntegrationLifecycleOrchestrator(self.workspace_id, connection, self.contract, registry)


def _action_for_target(target: ConnectionState) -> LifecycleAction:
    return {
        ConnectionState.AUTH_REQUIRED: LifecycleAction.AUTHENTICATE,
        ConnectionState.AUTHENTICATED: LifecycleAction.AUTHENTICATE,
        ConnectionState.VALIDATING: LifecycleAction.VALIDATE,
        ConnectionState.CAPABILITIES_DISCOVERED: LifecycleAction.DISCOVER_CAPABILITIES,
        ConnectionState.MAPPING_REQUIRED: LifecycleAction.MAP,
        ConnectionState.CERTIFICATION_REQUIRED: LifecycleAction.CERTIFY,
        ConnectionState.OBSERVE_ONLY: LifecycleAction.OBSERVE,
        ConnectionState.SUGGEST_ENABLED: LifecycleAction.SUGGEST,
        ConnectionState.EXECUTION_ENABLED: LifecycleAction.EXECUTE,
        ConnectionState.DEGRADED: LifecycleAction.DEGRADE,
        ConnectionState.SUSPENDED: LifecycleAction.SUSPEND,
        ConnectionState.REVOKED: LifecycleAction.REVOKE,
        ConnectionState.DISCOVERED: LifecycleAction.DISCOVER,
    }[target]


__all__ = [
    "LifecycleBlock", "LifecycleAction", "LifecycleTransition", "LifecycleSnapshot",
    "IntegrationLifecycleOrchestrator",
]
