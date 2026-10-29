"""Phase 8 — business-level reliability contracts.

This module is a read-model contract over KDO's existing health, SLO,
reconciliation and recovery primitives. It never executes recovery and never
grants execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Tuple
from hashlib import sha256

SCHEMA_VERSION = "KDO.BusinessReliability/v1"


class ReliabilityStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class ReliabilitySignalKind(str, Enum):
    SLO = "SLO"
    DEPENDENCY = "DEPENDENCY"
    DRIFT = "DRIFT"
    RECOVERY = "RECOVERY"
    RECONCILIATION_DEBT = "RECONCILIATION_DEBT"


@dataclass(frozen=True)
class BusinessReliabilityObjective:
    workspace_id: str
    resource_ref: str
    availability_target: float | None = None
    success_rate_target: float | None = None
    max_reconciliation_debt: int = 0
    max_recovery_age_seconds: int | None = None
    objective_id: str = ""
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        for name, value in (("availability_target", self.availability_target),
                            ("success_rate_target", self.success_rate_target)):
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_reconciliation_debt < 0:
            raise ValueError("max_reconciliation_debt cannot be negative")
        if self.max_recovery_age_seconds is not None and self.max_recovery_age_seconds < 0:
            raise ValueError("max_recovery_age_seconds cannot be negative")
        if self.authority != "none":
            raise ValueError("reliability objectives cannot carry authority")


@dataclass(frozen=True)
class ReliabilitySignal:
    workspace_id: str
    resource_ref: str
    kind: ReliabilitySignalKind
    value: float | int | str | bool
    healthy: bool | None
    evidence_ref: str | None = None
    correlation_id: str | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    signal_id: str = ""
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if self.evidence_ref is not None and not self.evidence_ref.strip():
            raise ValueError("evidence_ref cannot be blank")
        if self.authority != "none":
            raise ValueError("reliability signals cannot carry authority")


@dataclass(frozen=True)
class BusinessReliabilityAssessment:
    workspace_id: str
    resource_ref: str
    status: ReliabilityStatus
    objective_id: str
    signal_ids: Tuple[str, ...]
    breached_signals: Tuple[str, ...]
    unknown_signals: Tuple[str, ...]
    reconciliation_debt: int
    recovery_blocked: bool
    summary: str
    fingerprint: str
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    @property
    def safe_for_execution(self) -> bool:
        return self.status == ReliabilityStatus.HEALTHY and not self.recovery_blocked

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "resource_ref": self.resource_ref,
            "status": self.status.value,
            "objective_id": self.objective_id,
            "signal_ids": list(self.signal_ids),
            "breached_signals": list(self.breached_signals),
            "unknown_signals": list(self.unknown_signals),
            "reconciliation_debt": self.reconciliation_debt,
            "recovery_blocked": self.recovery_blocked,
            "summary": self.summary,
            "fingerprint": self.fingerprint,
            "authority": self.authority,
        }


def _fingerprint(payload: Mapping[str, object]) -> str:
    canonical = repr(sorted(payload.items())).encode("utf-8")
    return sha256(canonical).hexdigest()


def assess_business_reliability(
    objective: BusinessReliabilityObjective,
    signals: Tuple[ReliabilitySignal, ...],
    *,
    reconciliation_debt: int = 0,
    recovery_blocked: bool = False,
) -> BusinessReliabilityAssessment:
    for signal in signals:
        if signal.workspace_id != objective.workspace_id:
            raise ValueError("cross-workspace reliability signal is forbidden")
        if signal.resource_ref != objective.resource_ref:
            raise ValueError("signal resource does not match objective")
    if reconciliation_debt < 0:
        raise ValueError("reconciliation_debt cannot be negative")

    ordered = tuple(sorted(signals, key=lambda s: (s.kind.value, s.signal_id, str(s.value))))
    breached = []
    unknown = []
    for signal in ordered:
        if signal.healthy is False:
            breached.append(signal.signal_id)
        elif signal.healthy is None:
            unknown.append(signal.signal_id)

    if reconciliation_debt > objective.max_reconciliation_debt or recovery_blocked:
        status = ReliabilityStatus.BLOCKED
    elif unknown:
        status = ReliabilityStatus.UNKNOWN
    elif breached:
        status = ReliabilityStatus.CRITICAL if len(breached) >= 2 else ReliabilityStatus.DEGRADED
    else:
        status = ReliabilityStatus.HEALTHY

    summary = (
        "business reliability objective satisfied"
        if status == ReliabilityStatus.HEALTHY
        else f"business reliability status={status.value}; breaches={len(breached)}; unknown={len(unknown)}; reconciliation_debt={reconciliation_debt}"
    )
    fp = _fingerprint({
        "workspace_id": objective.workspace_id,
        "resource_ref": objective.resource_ref,
        "objective_id": objective.objective_id,
        "status": status.value,
        "signals": tuple((s.signal_id, s.kind.value, s.value, s.healthy) for s in ordered),
        "reconciliation_debt": reconciliation_debt,
        "recovery_blocked": recovery_blocked,
    })
    return BusinessReliabilityAssessment(
        workspace_id=objective.workspace_id,
        resource_ref=objective.resource_ref,
        status=status,
        objective_id=objective.objective_id,
        signal_ids=tuple(s.signal_id for s in ordered),
        breached_signals=tuple(breached),
        unknown_signals=tuple(unknown),
        reconciliation_debt=reconciliation_debt,
        recovery_blocked=recovery_blocked,
        summary=summary,
        fingerprint=fp,
    )
