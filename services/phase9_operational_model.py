"""Phase 9 canonical operational evidence model.

This module is deliberately authority-free. It defines the shared vocabulary for
operational facts and dependency state without becoming an execution engine.
Authoritative business/execution state remains in the existing Action Kernel,
Execution Ledger, Connector Fabric, verification and reconciliation boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional
import uuid


class OperationalState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    RECOVERING = "RECOVERING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SECURITY_HOLD = "SECURITY_HOLD"
    QUARANTINED = "QUARANTINED"


class OperationalEventType(str, Enum):
    DEPENDENCY_HEALTH_OBSERVED = "DEPENDENCY_HEALTH_OBSERVED"
    DEPENDENCY_STATE_CHANGED = "DEPENDENCY_STATE_CHANGED"
    EXECUTION_STATE_OBSERVED = "EXECUTION_STATE_OBSERVED"
    RECEIPT_OBSERVED = "RECEIPT_OBSERVED"
    VERIFICATION_OBSERVED = "VERIFICATION_OBSERVED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILIATION_COMPLETED = "RECONCILIATION_COMPLETED"
    INCIDENT_OPENED = "INCIDENT_OPENED"
    INCIDENT_UPDATED = "INCIDENT_UPDATED"
    INCIDENT_RESOLVED = "INCIDENT_RESOLVED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    SECURITY_HOLD_APPLIED = "SECURITY_HOLD_APPLIED"
    QUARANTINE_APPLIED = "QUARANTINE_APPLIED"


@dataclass(frozen=True)
class OperationalEvent:
    """Immutable operational fact; never a command and never an authority grant."""

    event_type: OperationalEventType
    workspace_id: str
    occurred_at: str
    source: str
    state: Optional[OperationalState] = None
    previous_state: Optional[OperationalState] = None
    event_id: str = field(default_factory=lambda: f"ope_{uuid.uuid4().hex}")
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    execution_id: Optional[str] = None
    dependency_id: Optional[str] = None
    incident_id: Optional[str] = None
    provider: Optional[str] = None
    evidence_ref: Optional[str] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "phase9.operational.v1"
    authority: str = "none"

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.source.strip():
            raise ValueError("source is required")
        if self.authority != "none":
            raise ValueError("operational events cannot grant authority")
        if self.previous_state == self.state and self.previous_state is not None:
            raise ValueError("state-change event cannot repeat the same state")
        if self.event_type == OperationalEventType.DEPENDENCY_STATE_CHANGED:
            if self.state is None or self.previous_state is None:
                raise ValueError("dependency state changes require previous_state and state")
        if self.event_type in {
            OperationalEventType.RECONCILIATION_REQUIRED,
            OperationalEventType.RECONCILIATION_COMPLETED,
        } and not self.execution_id and not self.dependency_id:
            raise ValueError("reconciliation event requires execution_id or dependency_id")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# Conservative transitions: UNKNOWN is deliberately not a success state.
_ALLOWED_TRANSITIONS: dict[OperationalState, frozenset[OperationalState]] = {
    OperationalState.HEALTHY: frozenset({
        OperationalState.DEGRADED,
        OperationalState.UNAVAILABLE,
        OperationalState.UNKNOWN,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
    }),
    OperationalState.DEGRADED: frozenset({
        OperationalState.HEALTHY,
        OperationalState.UNAVAILABLE,
        OperationalState.UNKNOWN,
        OperationalState.RECOVERING,
        OperationalState.RECONCILIATION_REQUIRED,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
    }),
    OperationalState.UNAVAILABLE: frozenset({
        OperationalState.RECOVERING,
        OperationalState.UNKNOWN,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
    }),
    OperationalState.UNKNOWN: frozenset({
        OperationalState.DEGRADED,
        OperationalState.UNAVAILABLE,
        OperationalState.RECOVERING,
        OperationalState.RECONCILIATION_REQUIRED,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
    }),
    OperationalState.RECOVERING: frozenset({
        OperationalState.HEALTHY,
        OperationalState.DEGRADED,
        OperationalState.UNKNOWN,
        OperationalState.RECONCILIATION_REQUIRED,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
    }),
    OperationalState.RECONCILIATION_REQUIRED: frozenset({
        OperationalState.RECOVERING,
        OperationalState.DEGRADED,
        OperationalState.HEALTHY,
        OperationalState.UNKNOWN,
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
    }),
    OperationalState.SECURITY_HOLD: frozenset({
        OperationalState.QUARANTINED,
        OperationalState.RECOVERING,
        OperationalState.UNKNOWN,
    }),
    OperationalState.QUARANTINED: frozenset({
        OperationalState.RECOVERING,
        OperationalState.SECURITY_HOLD,
        OperationalState.UNKNOWN,
    }),
}


def validate_transition(previous: OperationalState, new: OperationalState) -> None:
    """Validate an operational-state transition; does not mutate or authorize."""
    if previous == new:
        return
    if new not in _ALLOWED_TRANSITIONS[previous]:
        raise ValueError(f"invalid operational transition: {previous.value} -> {new.value}")


def can_execute_safely(state: OperationalState, *, requires_authoritative_truth: bool = True) -> bool:
    """Policy input only; the caller must still pass normal governance/authorization."""
    if state in {
        OperationalState.SECURITY_HOLD,
        OperationalState.QUARANTINED,
        OperationalState.UNAVAILABLE,
        OperationalState.UNKNOWN,
        OperationalState.RECONCILIATION_REQUIRED,
    }:
        return False
    if requires_authoritative_truth and state != OperationalState.HEALTHY:
        return False
    return True


__all__ = [
    "OperationalState",
    "OperationalEventType",
    "OperationalEvent",
    "now_utc",
    "validate_transition",
    "can_execute_safely",
]
