"""Phase 3 — canonical business state and drift read-model contracts.

The model composes KDO's existing Phase 9 reconciliation primitives with the
Business Process & Capability Graph. It does not create a second state store,
reconciliation engine, or remediation executor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from services.phase9_reconciliation import DriftFinding, DriftSeverity, ReconciliationStatus


SCHEMA = "KDO.BusinessStateDrift/v1"


class BusinessStateHealth(str, Enum):
    MATCHED = "matched"
    DRIFTED = "drifted"
    UNKNOWN = "unknown"
    OBSERVATION_UNAVAILABLE = "observation_unavailable"
    RECONCILIATION_REQUIRED = "reconciliation_required"


@dataclass(frozen=True)
class BusinessStateResource:
    workspace_id: str
    resource_ref: str
    node_id: str | None = None
    node_type: str | None = None
    desired_state: Mapping[str, Any] = field(default_factory=dict)
    observed_state: Mapping[str, Any] | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if not isinstance(self.desired_state, Mapping):
            raise TypeError("desired_state must be a mapping")
        if self.observed_state is not None and not isinstance(self.observed_state, Mapping):
            raise TypeError("observed_state must be a mapping")


@dataclass(frozen=True)
class BusinessStateDriftFinding:
    resource: BusinessStateResource
    finding: DriftFinding

    @property
    def health(self) -> BusinessStateHealth:
        mapping = {
            ReconciliationStatus.MATCHED: BusinessStateHealth.MATCHED,
            ReconciliationStatus.DRIFT_DETECTED: BusinessStateHealth.DRIFTED,
            ReconciliationStatus.UNKNOWN: BusinessStateHealth.UNKNOWN,
            ReconciliationStatus.OBSERVATION_UNAVAILABLE: BusinessStateHealth.OBSERVATION_UNAVAILABLE,
            ReconciliationStatus.RECONCILIATION_REQUIRED: BusinessStateHealth.RECONCILIATION_REQUIRED,
            ReconciliationStatus.RESOLVED: BusinessStateHealth.MATCHED,
        }
        return mapping[self.finding.status]

    @property
    def severity(self) -> DriftSeverity:
        return self.finding.severity

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "workspace_id": self.resource.workspace_id,
            "resource_ref": self.resource.resource_ref,
            "node_id": self.resource.node_id,
            "node_type": self.resource.node_type,
            "health": self.health.value,
            "severity": self.severity.value,
            "status": self.finding.status.value,
            "differing_fields": list(self.finding.differing_fields),
            "evidence_refs": list(self.finding.evidence_refs),
            "expected_id": self.finding.expected_id,
            "observation_id": self.finding.observation_id,
        }


@dataclass(frozen=True)
class BusinessStateDriftSnapshot:
    schema: str
    workspace_id: str
    findings: tuple[BusinessStateDriftFinding, ...]
    coverage: Mapping[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if self.schema != SCHEMA:
            raise ValueError("unsupported business state drift schema")

    @property
    def drifted(self) -> tuple[BusinessStateDriftFinding, ...]:
        return tuple(f for f in self.findings if f.health == BusinessStateHealth.DRIFTED)

    @property
    def blocks_unsafe_execution(self) -> bool:
        return any(f.finding.blocks_unsafe_execution for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workspace_id": self.workspace_id,
            "findings": [f.to_dict() for f in self.findings],
            "coverage": dict(self.coverage),
            "warnings": list(self.warnings),
            "authority": {
                "read_only": True,
                "does_not_execute": True,
                "does_not_remediate": True,
                "does_not_authorize": True,
                "reuses_phase9_reconciliation": True,
            },
        }


__all__ = [
    "SCHEMA",
    "BusinessStateHealth",
    "BusinessStateResource",
    "BusinessStateDriftFinding",
    "BusinessStateDriftSnapshot",
]
