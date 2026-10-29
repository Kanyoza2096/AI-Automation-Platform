"""KDO Phase 9.9 — Operational Intelligence & Reliability Synthesis.

Synthesizes Phase 9 evidence into a deterministic operational picture.
This module is descriptive/diagnostic only: it does not authorize, execute,
remediate, or mutate external state.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple
from uuid import uuid4


SCHEMA_VERSION = "phase9.operational-intelligence.v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class OperationalRisk(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class OperationalPosture(str, Enum):
    NORMAL = "NORMAL"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    DEGRADED = "DEGRADED"
    INCIDENT = "INCIDENT"
    RECOVERY = "RECOVERY"
    RECONCILIATION = "RECONCILIATION"
    SECURITY_HOLD = "SECURITY_HOLD"
    QUARANTINED = "QUARANTINED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OperationalSignal:
    workspace_id: str
    signal_type: str
    source_ref: str
    severity: OperationalRisk
    summary: str
    evidence_ref: Optional[str] = None
    correlation_id: Optional[str] = None
    signal_id: str = field(default_factory=lambda: _id("signal"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.signal_type.strip() or not self.source_ref.strip():
            raise ValueError("signal_type and source_ref are required")
        if not self.summary.strip():
            raise ValueError("summary is required")
        if self.authority != "none":
            raise ValueError("operational signal cannot carry authority")


@dataclass(frozen=True)
class OperationalSnapshot:
    workspace_id: str
    posture: OperationalPosture
    risk: OperationalRisk
    signal_count: int
    critical_signal_count: int
    incident_count: int
    degraded_dependency_count: int
    reconciliation_required_count: int
    recovery_required_count: int
    security_hold_count: int
    quarantine_count: int
    unknown_count: int
    dominant_causes: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]
    snapshot_id: str = field(default_factory=lambda: _id("snapshot"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        counts = (
            self.signal_count,
            self.critical_signal_count,
            self.incident_count,
            self.degraded_dependency_count,
            self.reconciliation_required_count,
            self.recovery_required_count,
            self.security_hold_count,
            self.quarantine_count,
            self.unknown_count,
        )
        if any(x < 0 for x in counts):
            raise ValueError("snapshot counts cannot be negative")
        if self.critical_signal_count > self.signal_count:
            raise ValueError("critical signals cannot exceed total signals")
        if self.authority != "none":
            raise ValueError("operational snapshot cannot carry authority")

    @property
    def requires_attention(self) -> bool:
        return self.posture != OperationalPosture.NORMAL

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "workspace_id": self.workspace_id,
            "posture": self.posture.value,
            "risk": self.risk.value,
            "signal_count": self.signal_count,
            "critical_signal_count": self.critical_signal_count,
            "incident_count": self.incident_count,
            "degraded_dependency_count": self.degraded_dependency_count,
            "reconciliation_required_count": self.reconciliation_required_count,
            "recovery_required_count": self.recovery_required_count,
            "security_hold_count": self.security_hold_count,
            "quarantine_count": self.quarantine_count,
            "unknown_count": self.unknown_count,
            "dominant_causes": list(self.dominant_causes),
            "evidence_refs": list(self.evidence_refs),
            "authority": self.authority,
        }


def synthesize(
    workspace_id: str,
    *,
    signals: Tuple[OperationalSignal, ...] = (),
    incident_count: int = 0,
    degraded_dependency_count: int = 0,
    reconciliation_required_count: int = 0,
    recovery_required_count: int = 0,
    security_hold_count: int = 0,
    quarantine_count: int = 0,
    unknown_count: int = 0,
    dominant_causes: Tuple[str, ...] = (),
) -> OperationalSnapshot:
    if not workspace_id.strip():
        raise ValueError("workspace_id is required")
    if any(s.workspace_id != workspace_id for s in signals):
        raise ValueError("cross-workspace operational synthesis is forbidden")

    counts = {
        "incident": incident_count,
        "degraded": degraded_dependency_count,
        "reconciliation": reconciliation_required_count,
        "recovery": recovery_required_count,
        "security": security_hold_count,
        "quarantine": quarantine_count,
        "unknown": unknown_count,
    }
    if any(v < 0 for v in counts.values()):
        raise ValueError("operational counts cannot be negative")

    critical = sum(1 for s in signals if s.severity == OperationalRisk.CRITICAL)
    high = sum(1 for s in signals if s.severity == OperationalRisk.HIGH)

    # Conservative precedence: security/quarantine/reconciliation/recovery
    # conditions outrank generic degradation.
    if quarantine_count:
        posture = OperationalPosture.QUARANTINED
        risk = OperationalRisk.CRITICAL
    elif security_hold_count:
        posture = OperationalPosture.SECURITY_HOLD
        risk = OperationalRisk.CRITICAL
    elif reconciliation_required_count:
        posture = OperationalPosture.RECONCILIATION
        risk = OperationalRisk.HIGH
    elif recovery_required_count:
        posture = OperationalPosture.RECOVERY
        risk = OperationalRisk.HIGH
    elif incident_count:
        posture = OperationalPosture.INCIDENT
        risk = OperationalRisk.HIGH if critical or high else OperationalRisk.MEDIUM
    elif degraded_dependency_count:
        posture = OperationalPosture.DEGRADED
        risk = OperationalRisk.MEDIUM
    elif unknown_count:
        posture = OperationalPosture.UNKNOWN
        risk = OperationalRisk.MEDIUM
    elif critical:
        posture = OperationalPosture.ATTENTION_REQUIRED
        risk = OperationalRisk.CRITICAL
    elif high:
        posture = OperationalPosture.ATTENTION_REQUIRED
        risk = OperationalRisk.HIGH
    elif signals:
        posture = OperationalPosture.ATTENTION_REQUIRED
        risk = OperationalRisk.LOW
    else:
        posture = OperationalPosture.NORMAL
        risk = OperationalRisk.NONE

    evidence = tuple(
        s.evidence_ref for s in signals if s.evidence_ref
    )

    return OperationalSnapshot(
        workspace_id=workspace_id,
        posture=posture,
        risk=risk,
        signal_count=len(signals),
        critical_signal_count=critical,
        incident_count=incident_count,
        degraded_dependency_count=degraded_dependency_count,
        reconciliation_required_count=reconciliation_required_count,
        recovery_required_count=recovery_required_count,
        security_hold_count=security_hold_count,
        quarantine_count=quarantine_count,
        unknown_count=unknown_count,
        dominant_causes=tuple(dominant_causes),
        evidence_refs=evidence,
    )


def compare_snapshots(
    previous: OperationalSnapshot,
    current: OperationalSnapshot,
) -> Tuple[str, ...]:
    """Return descriptive posture changes; never recommends an action."""
    if previous.workspace_id != current.workspace_id:
        raise ValueError("cross-workspace snapshot comparison is forbidden")

    changes = []
    if previous.posture != current.posture:
        changes.append(
            f"posture:{previous.posture.value}->{current.posture.value}"
        )
    if previous.risk != current.risk:
        changes.append(f"risk:{previous.risk.value}->{current.risk.value}")
    if previous.incident_count != current.incident_count:
        changes.append(
            f"incidents:{previous.incident_count}->{current.incident_count}"
        )
    if (
        previous.reconciliation_required_count
        != current.reconciliation_required_count
    ):
        changes.append(
            "reconciliation_required:"
            f"{previous.reconciliation_required_count}"
            f"->{current.reconciliation_required_count}"
        )
    if previous.recovery_required_count != current.recovery_required_count:
        changes.append(
            f"recovery_required:{previous.recovery_required_count}"
            f"->{current.recovery_required_count}"
        )
    return tuple(changes)
