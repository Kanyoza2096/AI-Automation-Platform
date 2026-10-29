"""KDO Phase 9.4 — Reliability and Dependency State.

Dependency health is authoritative operational state for safety decisions,
but it is not an execution authority.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Tuple
from uuid import uuid4


SCHEMA_VERSION = "phase9.reliability-dependency.v1"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class DependencyState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    RECOVERING = "RECOVERING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    SECURITY_HOLD = "SECURITY_HOLD"
    QUARANTINED = "QUARANTINED"


UNSAFE_STATES = frozenset({
    DependencyState.DEGRADED,
    DependencyState.UNAVAILABLE,
    DependencyState.UNKNOWN,
    DependencyState.RECOVERING,
    DependencyState.RECONCILIATION_REQUIRED,
    DependencyState.SECURITY_HOLD,
    DependencyState.QUARANTINED,
})


class HealthEvidenceType(str, Enum):
    HEARTBEAT = "HEARTBEAT"
    PROVIDER_RESPONSE = "PROVIDER_RESPONSE"
    CONNECTOR_CHECK = "CONNECTOR_CHECK"
    INCIDENT = "INCIDENT"
    VERIFICATION = "VERIFICATION"
    RECONCILIATION = "RECONCILIATION"
    OPERATOR_OBSERVATION = "OPERATOR_OBSERVATION"


@dataclass(frozen=True)
class DependencyHealthObservation:
    workspace_id: str
    dependency_id: str
    state: DependencyState
    observed_at: datetime = field(default_factory=now_utc)
    evidence_type: HealthEvidenceType = HealthEvidenceType.CONNECTOR_CHECK
    evidence_ref: Optional[str] = None
    correlation_id: Optional[str] = None
    reason: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)
    observation_id: str = field(default_factory=lambda: _id("health"))
    schema_version: str = SCHEMA_VERSION
    authority: str = "none"

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.dependency_id.strip():
            raise ValueError("dependency_id is required")
        if self.authority != "none":
            raise ValueError("health observations cannot carry authority")
        if self.evidence_ref is not None and not self.evidence_ref.strip():
            raise ValueError("evidence_ref cannot be blank")
        if self.correlation_id is not None and not self.correlation_id.strip():
            raise ValueError("correlation_id cannot be blank")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "workspace_id": self.workspace_id,
            "dependency_id": self.dependency_id,
            "state": self.state.value,
            "observed_at": self.observed_at.isoformat(),
            "evidence_type": self.evidence_type.value,
            "evidence_ref": self.evidence_ref,
            "correlation_id": self.correlation_id,
            "reason": self.reason,
            "attributes": dict(self.attributes),
            "authority": self.authority,
        }


@dataclass(frozen=True)
class DependencyHealth:
    workspace_id: str
    dependency_id: str
    state: DependencyState
    last_observed_at: datetime
    evidence_ref: Optional[str]
    reason: str
    updated_at: datetime = field(default_factory=now_utc)
    revision: int = 1
    schema_version: str = SCHEMA_VERSION
    authority: str = "none"

    def __post_init__(self):
        if not self.workspace_id.strip() or not self.dependency_id.strip():
            raise ValueError("workspace_id and dependency_id are required")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.authority != "none":
            raise ValueError("dependency health cannot carry authority")

    @property
    def safe_for_execution(self) -> bool:
        return self.state == DependencyState.HEALTHY

    @property
    def requires_intervention(self) -> bool:
        return self.state in UNSAFE_STATES

    def apply(self, observation: DependencyHealthObservation) -> "DependencyHealth":
        if observation.workspace_id != self.workspace_id:
            raise ValueError("observation crosses workspace boundary")
        if observation.dependency_id != self.dependency_id:
            raise ValueError("observation targets a different dependency")
        if observation.observed_at < self.last_observed_at:
            raise ValueError("stale health observation cannot overwrite newer state")
        return DependencyHealth(
            workspace_id=self.workspace_id,
            dependency_id=self.dependency_id,
            state=observation.state,
            last_observed_at=observation.observed_at,
            evidence_ref=observation.evidence_ref,
            reason=observation.reason,
            revision=self.revision + 1,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "dependency_id": self.dependency_id,
            "state": self.state.value,
            "last_observed_at": self.last_observed_at.isoformat(),
            "evidence_ref": self.evidence_ref,
            "reason": self.reason,
            "updated_at": self.updated_at.isoformat(),
            "revision": self.revision,
            "authority": self.authority,
        }


def can_execute_safely(health: DependencyHealth, *,
                       requires_authoritative_dependency: bool = True) -> bool:
    """Conservative safety predicate; never grants execution authority."""
    if not requires_authoritative_dependency:
        return health.state not in {
            DependencyState.UNAVAILABLE,
            DependencyState.SECURITY_HOLD,
            DependencyState.QUARANTINED,
        }
    return health.state == DependencyState.HEALTHY


@dataclass(frozen=True)
class DependencyRegistry:
    workspace_id: str
    dependencies: Tuple[DependencyHealth, ...] = ()

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        for item in self.dependencies:
            if item.workspace_id != self.workspace_id:
                raise ValueError("dependency health crosses workspace boundary")

    def upsert(self, health: DependencyHealth) -> "DependencyRegistry":
        if health.workspace_id != self.workspace_id:
            raise ValueError("dependency health crosses workspace boundary")
        items = [x for x in self.dependencies if x.dependency_id != health.dependency_id]
        items.append(health)
        return replace(self, dependencies=tuple(items))

    def get(self, dependency_id: str) -> Optional[DependencyHealth]:
        for item in self.dependencies:
            if item.dependency_id == dependency_id:
                return item
        return None
