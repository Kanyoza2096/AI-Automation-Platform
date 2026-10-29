"""V68.1 bounded business autonomy contract.

This module defines a declarative operating envelope for a business objective.
It is NOT an authority grant and MUST NOT execute actions. Existing decision
policy, approval, Action Kernel, outbox, execution ledger, and recovery planes
remain authoritative for side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import uuid
from typing import Any, Mapping, Optional, Sequence


class AutonomyMode(str, Enum):
    OBSERVE = "observe"
    SUGGEST = "suggest"
    EXECUTE = "execute"


class EnvelopeState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class EscalationTrigger(str, Enum):
    HIGH_RISK = "high_risk"
    CRITICAL_RISK = "critical_risk"
    POLICY_REVIEW = "policy_review"
    LOW_CONFIDENCE = "low_confidence"
    BUDGET_EXCEEDED = "budget_exceeded"
    METRIC_FAILURE = "metric_failure"
    UNCERTAIN_EXTERNAL = "uncertain_external"
    REPEATED_FAILURE = "repeated_failure"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"


@dataclass(frozen=True)
class SuccessMetric:
    name: str
    operator: str
    target: float
    unit: str = ""
    minimum_confidence: float = 0.0

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("metric name is required")
        if self.operator not in {">=", "<=", "==", ">", "<"}:
            raise ValueError("unsupported metric operator")
        if not 0.0 <= float(self.minimum_confidence) <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "operator": self.operator,
            "target": float(self.target),
            "unit": self.unit,
            "minimum_confidence": float(self.minimum_confidence),
        }


@dataclass(frozen=True)
class RiskBoundary:
    max_risk: str = "medium"
    require_approval_for: tuple[str, ...] = ("high", "critical")
    prohibited_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        allowed = {"low", "medium", "high", "critical"}
        if self.max_risk not in allowed:
            raise ValueError("max_risk must be low, medium, high, or critical")
        if any(r not in allowed for r in self.require_approval_for):
            raise ValueError("invalid approval risk class")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_risk": self.max_risk,
            "require_approval_for": list(self.require_approval_for),
            "prohibited_actions": list(self.prohibited_actions),
        }


@dataclass(frozen=True)
class ResourceLimit:
    key: str
    limit: float
    period: str = "lifetime"

    def __post_init__(self) -> None:
        if not self.key.strip() or float(self.limit) < 0:
            raise ValueError("resource limit requires non-empty key and non-negative limit")
        if not self.period.strip():
            raise ValueError("resource limit period is required")

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "limit": float(self.limit), "period": self.period}


@dataclass(frozen=True)
class BusinessAutonomyEnvelope:
    """Bounded objective contract; authority is intentionally always `none`."""

    workspace_id: str
    objective: str
    success_metrics: tuple[SuccessMetric, ...]
    allowed_actions: tuple[str, ...] = ()
    scope: Mapping[str, Any] = field(default_factory=dict)
    risk_boundary: RiskBoundary = field(default_factory=RiskBoundary)
    resource_limits: tuple[ResourceLimit, ...] = ()
    escalation_triggers: tuple[EscalationTrigger, ...] = (
        EscalationTrigger.HIGH_RISK,
        EscalationTrigger.CRITICAL_RISK,
        EscalationTrigger.POLICY_REVIEW,
        EscalationTrigger.UNCERTAIN_EXTERNAL,
        EscalationTrigger.REPEATED_FAILURE,
        EscalationTrigger.EVIDENCE_INSUFFICIENT,
    )
    mode: AutonomyMode = AutonomyMode.SUGGEST
    valid_from: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: Optional[datetime] = None
    state: EnvelopeState = EnvelopeState.DRAFT
    goal_id: Optional[str] = None
    envelope_id: str = field(default_factory=lambda: f"env_{uuid.uuid4().hex[:16]}")
    version: int = 1

    # Explicitly non-authoritative fields. These are contractual declarations only.
    authority: str = field(default="none", init=False)
    execution_effect: str = field(default="none", init=False)
    approval_effect: str = field(default="none", init=False)
    policy_effect: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.objective.strip():
            raise ValueError("objective is required")
        if not self.success_metrics:
            raise ValueError("at least one success metric is required")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")
        if any(not str(a).strip() for a in self.allowed_actions):
            raise ValueError("allowed_actions cannot contain empty values")
        if self.valid_from.tzinfo is None:
            raise ValueError("valid_from must be timezone-aware")
        if self.valid_until is not None:
            if self.valid_until.tzinfo is None:
                raise ValueError("valid_until must be timezone-aware")
            if self.valid_until <= self.valid_from:
                raise ValueError("valid_until must be after valid_from")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.mode == AutonomyMode.EXECUTE and not self.allowed_actions:
            raise ValueError("execute mode requires an explicit non-empty action allowlist")
        if self.authority != "none" or self.execution_effect != "none" or self.approval_effect != "none" or self.policy_effect != "none":
            raise ValueError("autonomy envelope cannot grant authority or execution effects")

    def is_active(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.state == EnvelopeState.ACTIVE and self.valid_from <= now and (
            self.valid_until is None or now < self.valid_until
        )

    def is_action_declared(self, action_type: str) -> bool:
        return str(action_type) in self.allowed_actions and str(action_type) not in self.risk_boundary.prohibited_actions

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "objective": self.objective,
            "success_metrics": [m.to_dict() for m in self.success_metrics],
            "allowed_actions": list(self.allowed_actions),
            "scope": _canonicalize(self.scope),
            "risk_boundary": self.risk_boundary.to_dict(),
            "resource_limits": [r.to_dict() for r in self.resource_limits],
            "escalation_triggers": [e.value for e in self.escalation_triggers],
            "mode": self.mode.value,
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "state": self.state.value,
            "goal_id": self.goal_id,
            "envelope_id": self.envelope_id,
            "version": self.version,
            "authority": "none",
            "execution_effect": "none",
            "approval_effect": "none",
            "policy_effect": "none",
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = self.canonical_dict()
        result["fingerprint"] = self.fingerprint()
        return result


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(value[k]) for k in sorted(value, key=lambda x: str(x))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def evaluate_envelope_for_action(
    envelope: BusinessAutonomyEnvelope,
    *,
    workspace_id: str,
    action_type: str,
    risk: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Read-only declaration check; never authorizes execution."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if envelope.workspace_id != workspace_id:
        reasons.append("workspace_mismatch")
    if not envelope.is_active(now):
        reasons.append("envelope_not_active")
    if not envelope.is_action_declared(action_type):
        reasons.append("action_not_declared")
    risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    if risk not in risk_order:
        reasons.append("unknown_risk")
    elif risk_order[risk] > risk_order[envelope.risk_boundary.max_risk]:
        reasons.append("risk_exceeds_envelope")
    if risk in envelope.risk_boundary.require_approval_for:
        reasons.append("approval_required_by_envelope")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "authority": "none",
        "execution_effect": "none",
        "approval_effect": "none",
        "policy_effect": "none",
        "requires_authoritative_recheck": True,
    }
