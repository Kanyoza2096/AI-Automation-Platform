"""V69.15 — governed integration policy and approval plane.

This module decides *what approval level is required* for an integration action.
It never grants execution authority and never calls a provider.  AI may supply
context, but the resulting policy decision is deterministic and conservative.
Actual authority remains with the Action Kernel/runtime gateway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Iterable, Mapping
from uuid import uuid4

from council.policies.action_policy import ActionClass, ActionPolicy
from council.policies.autonomy_policy import AutonomyLevel, AutonomyPolicy
from services.business_integration_platform import AutonomyMode, ConnectionState, ConnectorContract, IntegrationConnection
from services.v69_connector_activation_registry import ConnectorRegistryRecord, RegistryState
from services.v69_integration_evidence import EvidenceSink, emit


class ApprovalLevel(str, Enum):
    NONE = "none"
    SUGGESTION = "suggestion"
    COUNCIL = "council"
    HUMAN = "human"
    FORBIDDEN = "forbidden"


class PolicyBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    CONNECTION_NOT_READY = "connection_not_ready"
    CAPABILITY_NOT_GRANTED = "capability_not_granted"
    ACTION_NOT_DECLARED = "action_not_declared"
    REGISTRY_NOT_ACTIVE = "registry_not_active"
    CERTIFICATION_MISSING = "certification_missing"
    POLICY_DENIED = "policy_denied"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    COUNCIL_APPROVAL_REQUIRED = "council_approval_required"
    AUTONOMY_INSUFFICIENT = "autonomy_insufficient"
    IRREVERSIBLE_ACTION = "irreversible_action"
    HIGH_RISK_ACTION = "high_risk_action"
    EXPLICIT_DENY = "explicit_deny"


@dataclass(frozen=True)
class IntegrationPolicy:
    """Tenant-scoped business policy; it contains no credentials or authority."""
    workspace_id: str
    allowed_actions: FrozenSet[str] = frozenset()
    denied_actions: FrozenSet[str] = frozenset()
    council_actions: FrozenSet[str] = frozenset()
    human_actions: FrozenSet[str] = frozenset()
    forbidden_actions: FrozenSet[str] = frozenset()
    allow_autonomous_low_risk: bool = True
    policy_version: str = "1.0"

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError(PolicyBlock.TENANT_MISMATCH.value)
        overlap = (self.denied_actions | self.council_actions | self.human_actions | self.forbidden_actions) & self.allowed_actions
        if overlap:
            raise ValueError("policy_action_overlap")


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    workspace_id: str
    connection_id: str
    connector_key: str
    action: str
    capability: str
    level: ApprovalLevel
    risk: ActionClass
    reasons: tuple[str, ...] = ()
    policy_version: str = "1.0"
    authority_granted: bool = False

    def __post_init__(self) -> None:
        if self.authority_granted:
            raise ValueError("approval_request_cannot_grant_authority")


@dataclass(frozen=True)
class PolicyDecision:
    allowed_to_proceed: bool
    approval: ApprovalRequest
    reasons: tuple[str, ...] = ()
    authority_granted: bool = False
    execution_effect: str = "none"


class IntegrationPolicyPlane:
    """Pure-ish policy evaluator bound to one tenant/connection."""
    def __init__(self, workspace_id: str, connection: IntegrationConnection,
                 contract: ConnectorContract, registry: ConnectorRegistryRecord | None = None,
                 policy: IntegrationPolicy | None = None, evidence_sink: EvidenceSink | None = None):
        if connection.workspace_id != workspace_id or (registry is not None and registry.workspace_id != workspace_id):
            raise ValueError(PolicyBlock.TENANT_MISMATCH.value)
        if connection.connector_key != contract.connector_key or (registry is not None and registry.connector_key != contract.connector_key):
            raise ValueError(PolicyBlock.TENANT_MISMATCH.value)
        if policy is not None and policy.workspace_id != workspace_id:
            raise ValueError(PolicyBlock.TENANT_MISMATCH.value)
        self.workspace_id = workspace_id
        self.connection = connection
        self.contract = contract
        self.registry = registry
        self.policy = policy or IntegrationPolicy(workspace_id=workspace_id)
        self.evidence_sink = evidence_sink
        self._action_policy = ActionPolicy()
        self._autonomy_policy = AutonomyPolicy()

    def evaluate(self, action: str, *, capability: str | None = None,
                 context: Mapping[str, object] | None = None) -> PolicyDecision:
        action = action.strip().lower()
        capability = (capability or action).strip().lower()
        reasons: list[str] = []
        risk = self._action_policy.classify(action)
        autonomy = self._autonomy_policy.get_level(action)

        if self.connection.state != ConnectionState.EXECUTION_ENABLED:
            reasons.append(PolicyBlock.CONNECTION_NOT_READY.value)
        if not self.connection.has_capability(capability):
            reasons.append(PolicyBlock.CAPABILITY_NOT_GRANTED.value)
        if action not in self.contract.outbound_actions:
            reasons.append(PolicyBlock.ACTION_NOT_DECLARED.value)
        if self.registry is None or self.registry.state != RegistryState.ACTIVE:
            reasons.append(PolicyBlock.REGISTRY_NOT_ACTIVE.value)
        elif self.registry.evidence is None:
            reasons.append(PolicyBlock.CERTIFICATION_MISSING.value)

        # Explicit business policy can only tighten the platform defaults.
        if action in self.policy.forbidden_actions:
            level = ApprovalLevel.FORBIDDEN
            reasons.append(PolicyBlock.EXPLICIT_DENY.value)
        elif action in self.policy.denied_actions:
            level = ApprovalLevel.FORBIDDEN
            reasons.append(PolicyBlock.POLICY_DENIED.value)
        else:
            level = self._base_level(risk, autonomy)
            if action in self.policy.human_actions:
                level = self._max_level(level, ApprovalLevel.HUMAN)
            elif action in self.policy.council_actions:
                level = self._max_level(level, ApprovalLevel.COUNCIL)

        # Never allow policy configuration to lower the platform's risk floor.
        if risk in {ActionClass.CRITICAL, ActionClass.IRREVERSIBLE}:
            level = ApprovalLevel.HUMAN if risk != ActionClass.IRREVERSIBLE else ApprovalLevel.FORBIDDEN if autonomy == AutonomyLevel.FORBIDDEN else ApprovalLevel.HUMAN
            reasons.append(PolicyBlock.IRREVERSIBLE_ACTION.value if risk == ActionClass.IRREVERSIBLE else PolicyBlock.HIGH_RISK_ACTION.value)
        elif risk == ActionClass.HIGH_RISK:
            level = self._max_level(level, ApprovalLevel.HUMAN if autonomy == AutonomyLevel.HUMAN_APPROVAL else ApprovalLevel.COUNCIL)
            reasons.append(PolicyBlock.HIGH_RISK_ACTION.value)

        if autonomy == AutonomyLevel.FORBIDDEN:
            level = ApprovalLevel.FORBIDDEN
            reasons.append(PolicyBlock.EXPLICIT_DENY.value)
        elif autonomy == AutonomyLevel.HUMAN_APPROVAL:
            level = self._max_level(level, ApprovalLevel.HUMAN)
            reasons.append(PolicyBlock.HUMAN_APPROVAL_REQUIRED.value)
        elif autonomy == AutonomyLevel.COUNCIL_APPROVAL:
            level = self._max_level(level, ApprovalLevel.COUNCIL)
            reasons.append(PolicyBlock.COUNCIL_APPROVAL_REQUIRED.value)

        # EXECUTE mode never bypasses required approval. Observe/suggest cannot execute.
        if self.connection.autonomy_mode != AutonomyMode.EXECUTE and level in {ApprovalLevel.NONE, ApprovalLevel.SUGGESTION}:
            reasons.append(PolicyBlock.AUTONOMY_INSUFFICIENT.value)
            level = ApprovalLevel.SUGGESTION

        blocked = any(r in reasons for r in (
            PolicyBlock.CONNECTION_NOT_READY.value, PolicyBlock.CAPABILITY_NOT_GRANTED.value,
            PolicyBlock.ACTION_NOT_DECLARED.value, PolicyBlock.REGISTRY_NOT_ACTIVE.value,
            PolicyBlock.CERTIFICATION_MISSING.value, PolicyBlock.POLICY_DENIED.value,
            PolicyBlock.EXPLICIT_DENY.value, PolicyBlock.AUTONOMY_INSUFFICIENT.value,
        )) or level == ApprovalLevel.FORBIDDEN
        approval = ApprovalRequest(str(uuid4()), self.workspace_id, self.connection.connection_id,
                                   self.contract.connector_key, action, capability, level, risk,
                                   tuple(dict.fromkeys(reasons)), self.policy.policy_version)
        decision = PolicyDecision(not blocked, approval, approval.reasons)
        if self.evidence_sink is not None:
            emit(self.evidence_sink, evidence_id=str(uuid4()), workspace_id=self.workspace_id,
                 connection_id=self.connection.connection_id, connector_key=self.contract.connector_key,
                 event_type="policy_decision", outcome="allowed" if decision.allowed_to_proceed else "blocked",
                 action=action, reason_codes=decision.reasons,
                 metadata={"approval_level": level.value, "risk": risk.value, "policy_version": self.policy.policy_version})
        return decision

    @staticmethod
    def _base_level(risk: ActionClass, autonomy: AutonomyLevel) -> ApprovalLevel:
        if risk == ActionClass.READ_ONLY:
            return ApprovalLevel.NONE
        if autonomy == AutonomyLevel.FULLY_AUTONOMOUS:
            return ApprovalLevel.NONE
        if autonomy == AutonomyLevel.HUMAN_APPROVAL:
            return ApprovalLevel.HUMAN
        if autonomy == AutonomyLevel.COUNCIL_APPROVAL:
            return ApprovalLevel.COUNCIL
        return ApprovalLevel.FORBIDDEN

    @staticmethod
    def _max_level(a: ApprovalLevel, b: ApprovalLevel) -> ApprovalLevel:
        order = {ApprovalLevel.NONE: 0, ApprovalLevel.SUGGESTION: 1, ApprovalLevel.COUNCIL: 2, ApprovalLevel.HUMAN: 3, ApprovalLevel.FORBIDDEN: 4}
        return a if order[a] >= order[b] else b


__all__ = ["ApprovalLevel", "PolicyBlock", "IntegrationPolicy", "ApprovalRequest", "PolicyDecision", "IntegrationPolicyPlane"]
