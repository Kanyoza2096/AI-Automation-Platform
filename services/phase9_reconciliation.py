"""KDO Phase 9.6 — Reconciliation & Drift Control.

This module models the comparison between expected platform state and
authoritative observed state. It produces evidence and a reconciliation
status; it never mutates external state or executes remediation.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple
from uuid import uuid4


SCHEMA_VERSION = "phase9.reconciliation-drift.v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    UNKNOWN = "UNKNOWN"
    OBSERVATION_UNAVAILABLE = "OBSERVATION_UNAVAILABLE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RESOLVED = "RESOLVED"


class DriftSeverity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ExpectedState:
    workspace_id: str
    resource_ref: str
    state: Mapping[str, object]
    source_execution_id: Optional[str] = None
    source_evidence_ref: Optional[str] = None
    expected_id: str = field(default_factory=lambda: _id("expected"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if not isinstance(self.state, Mapping):
            raise TypeError("state must be a mapping")
        if self.authority != "none":
            raise ValueError("expected state cannot carry authority")


@dataclass(frozen=True)
class AuthoritativeObservation:
    workspace_id: str
    resource_ref: str
    state: Mapping[str, object]
    observed_at: str
    evidence_ref: str
    observation_id: str = field(default_factory=lambda: _id("obs"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if not self.observed_at.strip() or not self.evidence_ref.strip():
            raise ValueError("observed_at and evidence_ref are required")
        if not isinstance(self.state, Mapping):
            raise TypeError("state must be a mapping")
        if self.authority != "none":
            raise ValueError("observation record cannot carry authority")


@dataclass(frozen=True)
class DriftFinding:
    workspace_id: str
    resource_ref: str
    status: ReconciliationStatus
    severity: DriftSeverity
    differing_fields: Tuple[str, ...]
    expected_id: str
    observation_id: Optional[str]
    evidence_refs: Tuple[str, ...]
    finding_id: str = field(default_factory=lambda: _id("drift"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if self.authority != "none":
            raise ValueError("drift finding cannot carry authority")
        if self.status == ReconciliationStatus.DRIFT_DETECTED and not self.differing_fields:
            raise ValueError("drift requires differing fields")
        if self.status == ReconciliationStatus.DRIFT_DETECTED and not self.evidence_refs:
            raise ValueError("drift requires evidence")

    @property
    def blocks_unsafe_execution(self) -> bool:
        return self.status in {
            ReconciliationStatus.DRIFT_DETECTED,
            ReconciliationStatus.UNKNOWN,
            ReconciliationStatus.OBSERVATION_UNAVAILABLE,
            ReconciliationStatus.RECONCILIATION_REQUIRED,
        }

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "finding_id": self.finding_id,
            "workspace_id": self.workspace_id,
            "resource_ref": self.resource_ref,
            "status": self.status.value,
            "severity": self.severity.value,
            "differing_fields": list(self.differing_fields),
            "expected_id": self.expected_id,
            "observation_id": self.observation_id,
            "evidence_refs": list(self.evidence_refs),
            "authority": self.authority,
        }


def _severity(fields: Tuple[str, ...]) -> DriftSeverity:
    if not fields:
        return DriftSeverity.NONE
    if len(fields) == 1:
        return DriftSeverity.LOW
    if len(fields) <= 3:
        return DriftSeverity.MEDIUM
    return DriftSeverity.HIGH


def reconcile(
    expected: ExpectedState,
    observation: Optional[AuthoritativeObservation],
) -> DriftFinding:
    """Compare expected state with an authoritative observation.

    No mutation or remediation is performed.
    """
    if observation is None:
        return DriftFinding(
            workspace_id=expected.workspace_id,
            resource_ref=expected.resource_ref,
            status=ReconciliationStatus.OBSERVATION_UNAVAILABLE,
            severity=DriftSeverity.HIGH,
            differing_fields=(),
            expected_id=expected.expected_id,
            observation_id=None,
            evidence_refs=tuple(
                x for x in (expected.source_evidence_ref,) if x
            ),
        )

    if observation.workspace_id != expected.workspace_id:
        raise ValueError("cross-workspace reconciliation is forbidden")
    if observation.resource_ref != expected.resource_ref:
        raise ValueError("resource mismatch")

    keys = sorted(set(expected.state) | set(observation.state))
    differing = tuple(
        k for k in keys if expected.state.get(k) != observation.state.get(k)
    )

    if not differing:
        return DriftFinding(
            workspace_id=expected.workspace_id,
            resource_ref=expected.resource_ref,
            status=ReconciliationStatus.MATCHED,
            severity=DriftSeverity.NONE,
            differing_fields=(),
            expected_id=expected.expected_id,
            observation_id=observation.observation_id,
            evidence_refs=tuple(
                x for x in (expected.source_evidence_ref, observation.evidence_ref)
                if x
            ),
        )

    return DriftFinding(
        workspace_id=expected.workspace_id,
        resource_ref=expected.resource_ref,
        status=ReconciliationStatus.DRIFT_DETECTED,
        severity=_severity(differing),
        differing_fields=differing,
        expected_id=expected.expected_id,
        observation_id=observation.observation_id,
        evidence_refs=tuple(
            x for x in (expected.source_evidence_ref, observation.evidence_ref)
            if x
        ),
    )


def can_resume_after_reconciliation(finding: DriftFinding, resolved: bool) -> bool:
    """Only a resolved finding can satisfy the reconciliation boundary."""
    return (
        resolved
        and finding.status in {
            ReconciliationStatus.MATCHED,
            ReconciliationStatus.RESOLVED,
        }
    )
