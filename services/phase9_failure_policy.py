"""KDO Phase 9.5 — Failure, Degradation & Recovery Policy.

Policy evaluates operational conditions and produces a declarative control
decision. It never executes actions, grants authorization, or calls providers.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple
from uuid import uuid4

from .phase9_reliability import DependencyState


SCHEMA_VERSION = "phase9.failure-policy.v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    DEGRADE = "DEGRADE"
    HOLD = "HOLD"
    BLOCK = "BLOCK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SECURITY_HOLD = "SECURITY_HOLD"
    QUARANTINE = "QUARANTINE"


class OperationRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class RecoveryPolicy:
    workspace_id: str
    dependency_id: str
    policy_id: str = field(default_factory=lambda: _id("policy"))
    allow_low_risk_when_degraded: bool = False
    allow_read_only_when_degraded: bool = True
    require_reconciliation_before_resume: bool = True
    require_healthy_before_high_risk: bool = True
    require_healthy_before_critical: bool = True
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.dependency_id.strip():
            raise ValueError("dependency_id is required")
        if self.authority != "none":
            raise ValueError("recovery policy cannot carry authority")


@dataclass(frozen=True)
class PolicyEvaluation:
    workspace_id: str
    dependency_id: str
    dependency_state: DependencyState
    risk: OperationRisk
    read_only: bool
    decision: PolicyDecision
    reason: str
    policy_id: str
    evaluation_id: str = field(default_factory=lambda: _id("eval"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.dependency_id.strip():
            raise ValueError("workspace and dependency are required")
        if not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if self.authority != "none":
            raise ValueError("policy evaluation cannot carry authority")

    @property
    def permits_execution(self) -> bool:
        # Explicitly informational: a later governance/authorization boundary
        # must still approve and execute the ActionIntent.
        return self.decision == PolicyDecision.ALLOW

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "workspace_id": self.workspace_id,
            "dependency_id": self.dependency_id,
            "dependency_state": self.dependency_state.value,
            "risk": self.risk.value,
            "read_only": self.read_only,
            "decision": self.decision.value,
            "reason": self.reason,
            "policy_id": self.policy_id,
            "authority": self.authority,
        }


def evaluate(
    policy: RecoveryPolicy,
    *,
    dependency_state: DependencyState,
    risk: OperationRisk,
    read_only: bool = False,
) -> PolicyEvaluation:
    """Return a declarative safety decision; never execute or authorize."""
    if dependency_state == DependencyState.HEALTHY:
        decision = PolicyDecision.ALLOW
        reason = "dependency is healthy"
    elif dependency_state == DependencyState.DEGRADED:
        if read_only and policy.allow_read_only_when_degraded:
            decision = PolicyDecision.DEGRADE
            reason = "read-only operation permitted under degraded dependency state"
        elif risk == OperationRisk.LOW and policy.allow_low_risk_when_degraded:
            decision = PolicyDecision.DEGRADE
            reason = "low-risk operation permitted under degraded dependency state"
        else:
            decision = PolicyDecision.HOLD
            reason = "dependency degraded; operation held conservatively"
    elif dependency_state == DependencyState.RECOVERING:
        decision = PolicyDecision.RECOVERY_REQUIRED
        reason = "dependency is recovering; normal execution is paused"
    elif dependency_state == DependencyState.RECONCILIATION_REQUIRED:
        decision = PolicyDecision.RECONCILIATION_REQUIRED
        reason = "truth must be reconciled before dependent execution resumes"
    elif dependency_state == DependencyState.SECURITY_HOLD:
        decision = PolicyDecision.SECURITY_HOLD
        reason = "dependency is under security hold"
    elif dependency_state == DependencyState.QUARANTINED:
        decision = PolicyDecision.QUARANTINE
        reason = "dependency is quarantined"
    elif dependency_state == DependencyState.UNAVAILABLE:
        decision = PolicyDecision.BLOCK
        reason = "dependency is unavailable"
    elif dependency_state == DependencyState.UNKNOWN:
        decision = PolicyDecision.HOLD
        reason = "dependency health is unknown; healthy state cannot be assumed"
    else:
        raise ValueError(f"unsupported dependency state: {dependency_state}")

    if dependency_state == DependencyState.HEALTHY:
        if risk == OperationRisk.CRITICAL and not policy.require_healthy_before_critical:
            pass
        elif risk == OperationRisk.HIGH and not policy.require_healthy_before_high_risk:
            pass

    return PolicyEvaluation(
        workspace_id=policy.workspace_id,
        dependency_id=policy.dependency_id,
        dependency_state=dependency_state,
        risk=risk,
        read_only=read_only,
        decision=decision,
        reason=reason,
        policy_id=policy.policy_id,
    )


def recovery_transition_allowed(
    current: DependencyState,
    requested: DependencyState,
) -> bool:
    """Bounded reliability-state transition check."""
    allowed = {
        DependencyState.UNAVAILABLE: {
            DependencyState.RECOVERING,
            DependencyState.UNKNOWN,
            DependencyState.QUARANTINED,
        },
        DependencyState.DEGRADED: {
            DependencyState.HEALTHY,
            DependencyState.RECOVERING,
            DependencyState.UNAVAILABLE,
            DependencyState.RECONCILIATION_REQUIRED,
        },
        DependencyState.UNKNOWN: {
            DependencyState.HEALTHY,
            DependencyState.DEGRADED,
            DependencyState.UNAVAILABLE,
            DependencyState.RECOVERING,
            DependencyState.SECURITY_HOLD,
        },
        DependencyState.RECOVERING: {
            DependencyState.HEALTHY,
            DependencyState.DEGRADED,
            DependencyState.RECONCILIATION_REQUIRED,
            DependencyState.UNAVAILABLE,
        },
        DependencyState.RECONCILIATION_REQUIRED: {
            DependencyState.RECOVERING,
            DependencyState.HEALTHY,
            DependencyState.SECURITY_HOLD,
        },
        DependencyState.SECURITY_HOLD: {
            DependencyState.QUARANTINED,
            DependencyState.RECOVERING,
            DependencyState.UNKNOWN,
        },
        DependencyState.QUARANTINED: {
            DependencyState.RECOVERING,
            DependencyState.SECURITY_HOLD,
        },
        DependencyState.HEALTHY: {
            DependencyState.DEGRADED,
            DependencyState.UNAVAILABLE,
            DependencyState.UNKNOWN,
            DependencyState.RECONCILIATION_REQUIRED,
            DependencyState.SECURITY_HOLD,
            DependencyState.QUARANTINED,
        },
    }
    return requested in allowed[current]
