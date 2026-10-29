"""KDO Phase 9.8 — Disaster Recovery & Continuity Contracts.

Models recovery objectives, ordered recovery dependencies, backup/restore
evidence, continuity readiness, and recovery plans. No recovery action is
executed by this module.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple
from uuid import uuid4


SCHEMA_VERSION = "phase9.disaster-recovery.v1"


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class RecoveryReadiness(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class RecoveryArtifactType(str, Enum):
    BACKUP = "BACKUP"
    SNAPSHOT = "SNAPSHOT"
    CONFIGURATION = "CONFIGURATION"
    KEY_MATERIAL = "KEY_MATERIAL"
    AUDIT_EVIDENCE = "AUDIT_EVIDENCE"


class RecoveryVerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class RecoveryStepKind(str, Enum):
    RESTORE = "RESTORE"
    VERIFY = "VERIFY"
    RECONCILE = "RECONCILE"
    REENABLE = "REENABLE"


@dataclass(frozen=True)
class RecoveryObjective:
    workspace_id: str
    resource_ref: str
    rto_seconds: int
    rpo_seconds: int
    objective_id: str = field(default_factory=lambda: _id("objective"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if self.rto_seconds < 0 or self.rpo_seconds < 0:
            raise ValueError("RTO/RPO cannot be negative")
        if self.authority != "none":
            raise ValueError("recovery objective cannot carry authority")


@dataclass(frozen=True)
class RecoveryArtifact:
    workspace_id: str
    resource_ref: str
    artifact_type: RecoveryArtifactType
    evidence_ref: str
    created_at: str
    verification: RecoveryVerificationStatus
    artifact_id: str = field(default_factory=lambda: _id("artifact"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.resource_ref.strip():
            raise ValueError("workspace_id and resource_ref are required")
        if not self.evidence_ref.strip() or not self.created_at.strip():
            raise ValueError("evidence_ref and created_at are required")
        if self.authority != "none":
            raise ValueError("recovery artifact cannot carry authority")


@dataclass(frozen=True)
class RecoveryStep:
    step_id: str
    dependency_id: str
    kind: RecoveryStepKind
    order: int
    requires_verified_state: bool = True

    def __post_init__(self):
        if not self.step_id.strip() or not self.dependency_id.strip():
            raise ValueError("step_id and dependency_id are required")
        if self.order < 1:
            raise ValueError("recovery order starts at 1")


@dataclass(frozen=True)
class RecoveryPlan:
    workspace_id: str
    name: str
    steps: Tuple[RecoveryStep, ...]
    plan_id: str = field(default_factory=lambda: _id("plan"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.name.strip():
            raise ValueError("workspace_id and name are required")
        if self.authority != "none":
            raise ValueError("recovery plan cannot carry authority")
        orders = [s.order for s in self.steps]
        if len(orders) != len(set(orders)):
            raise ValueError("recovery step order must be unique")

    def ordered_steps(self) -> Tuple[RecoveryStep, ...]:
        return tuple(sorted(self.steps, key=lambda s: s.order))


@dataclass(frozen=True)
class RecoveryAssessment:
    workspace_id: str
    resource_ref: str
    readiness: RecoveryReadiness
    objective: RecoveryObjective
    artifact_ids: Tuple[str, ...]
    failed_artifacts: Tuple[str, ...]
    verified_artifacts: Tuple[str, ...]
    assessment_id: str = field(default_factory=lambda: _id("assessment"))
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if self.authority != "none":
            raise ValueError("recovery assessment cannot carry authority")

    @property
    def can_resume(self) -> bool:
        return self.readiness == RecoveryReadiness.READY


def assess_recovery(
    objective: RecoveryObjective,
    artifacts: Tuple[RecoveryArtifact, ...],
) -> RecoveryAssessment:
    for artifact in artifacts:
        if artifact.workspace_id != objective.workspace_id:
            raise ValueError("cross-workspace recovery evidence is forbidden")
        if artifact.resource_ref != objective.resource_ref:
            raise ValueError("artifact resource does not match objective")

    ids = tuple(a.artifact_id for a in artifacts)
    failed = tuple(
        a.artifact_id for a in artifacts
        if a.verification == RecoveryVerificationStatus.FAILED
    )
    verified = tuple(
        a.artifact_id for a in artifacts
        if a.verification == RecoveryVerificationStatus.VERIFIED
    )

    required = {
        RecoveryArtifactType.BACKUP,
        RecoveryArtifactType.CONFIGURATION,
    }
    present_verified = {
        a.artifact_type for a in artifacts
        if a.verification == RecoveryVerificationStatus.VERIFIED
    }

    if failed:
        readiness = RecoveryReadiness.BLOCKED
    elif required.issubset(present_verified):
        readiness = RecoveryReadiness.READY
    elif not artifacts:
        readiness = RecoveryReadiness.UNKNOWN
    else:
        readiness = RecoveryReadiness.DEGRADED

    return RecoveryAssessment(
        workspace_id=objective.workspace_id,
        resource_ref=objective.resource_ref,
        readiness=readiness,
        objective=objective,
        artifact_ids=ids,
        failed_artifacts=failed,
        verified_artifacts=verified,
    )


def validate_recovery_plan(
    plan: RecoveryPlan,
    *,
    verified_dependencies: Tuple[str, ...] = (),
) -> bool:
    """Validate ordering and verification gates without executing steps."""
    verified = set(verified_dependencies)
    ordered = plan.ordered_steps()

    for step in ordered:
        if step.requires_verified_state and step.dependency_id not in verified:
            return False
    return True


def continuity_window_met(
    *,
    objective: RecoveryObjective,
    elapsed_recovery_seconds: int,
    data_loss_seconds: int,
) -> bool:
    if elapsed_recovery_seconds < 0 or data_loss_seconds < 0:
        raise ValueError("recovery measurements cannot be negative")
    return (
        elapsed_recovery_seconds <= objective.rto_seconds
        and data_loss_seconds <= objective.rpo_seconds
    )
