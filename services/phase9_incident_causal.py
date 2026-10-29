"""KDO Phase 9.3 — Incident and Causal Graph model.

Authority boundary:
- incidents and causal edges are immutable evidence-bearing operational records
- this module has no authorization or execution authority
- correlation does not imply causation
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Optional, Tuple
from uuid import uuid4


SCHEMA_VERSION = "phase9.incident-causal-graph.v1"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class IncidentSeverity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    CONTAINED = "CONTAINED"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CausalRelationType(str, Enum):
    CAUSED_BY = "CAUSED_BY"
    TRIGGERED_BY = "TRIGGERED_BY"
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKED_BY = "BLOCKED_BY"
    AFFECTS = "AFFECTS"
    CORRELATED_WITH = "CORRELATED_WITH"
    RECOVERED_BY = "RECOVERED_BY"
    RECONCILED_BY = "RECONCILED_BY"


_STATUS_TRANSITIONS = {
    IncidentStatus.OPEN: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.CONTAINED,
        IncidentStatus.RESOLVED,
        IncidentStatus.CLOSED,
    },
    IncidentStatus.INVESTIGATING: {
        IncidentStatus.CONTAINED,
        IncidentStatus.RECOVERING,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.CONTAINED: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.RECOVERING,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.RECOVERING: {
        IncidentStatus.INVESTIGATING,
        IncidentStatus.RESOLVED,
    },
    IncidentStatus.RESOLVED: {
        IncidentStatus.CLOSED,
        IncidentStatus.INVESTIGATING,
    },
    IncidentStatus.CLOSED: set(),
}


def validate_incident_transition(
    current: IncidentStatus, requested: IncidentStatus
) -> bool:
    return requested in _STATUS_TRANSITIONS[current]


@dataclass(frozen=True)
class IncidentRecord:
    workspace_id: str
    title: str
    summary: str
    severity: IncidentSeverity
    opened_at: datetime = field(default_factory=now_utc)
    incident_id: str = field(default_factory=lambda: _id("inc"))
    status: IncidentStatus = IncidentStatus.OPEN
    correlation_id: Optional[str] = None
    primary_evidence_ref: Optional[str] = None
    dependency_id: Optional[str] = None
    execution_id: Optional[str] = None
    causation_id: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    authority: str = "none"

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.title.strip():
            raise ValueError("title is required")
        if not self.summary.strip():
            raise ValueError("summary is required")
        if self.authority != "none":
            raise ValueError("incident records cannot carry authority")
        if self.correlation_id is not None and not self.correlation_id.strip():
            raise ValueError("correlation_id cannot be blank")
        if self.primary_evidence_ref is not None and not self.primary_evidence_ref.strip():
            raise ValueError("primary_evidence_ref cannot be blank")

    def transition(self, requested: IncidentStatus) -> "IncidentRecord":
        if not validate_incident_transition(self.status, requested):
            raise ValueError(
                f"invalid incident transition: {self.status.value} -> {requested.value}"
            )
        return replace(self, status=requested)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "workspace_id": self.workspace_id,
            "title": self.title,
            "summary": self.summary,
            "severity": self.severity.value,
            "status": self.status.value,
            "opened_at": self.opened_at.isoformat(),
            "correlation_id": self.correlation_id,
            "primary_evidence_ref": self.primary_evidence_ref,
            "dependency_id": self.dependency_id,
            "execution_id": self.execution_id,
            "causation_id": self.causation_id,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class CausalEdge:
    workspace_id: str
    source_ref: str
    target_ref: str
    relation: CausalRelationType
    correlation_id: str
    evidence_ref: str
    edge_id: str = field(default_factory=lambda: _id("edge"))
    created_at: datetime = field(default_factory=now_utc)
    authority: str = "none"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not self.source_ref.strip() or not self.target_ref.strip():
            raise ValueError("source_ref and target_ref are required")
        if self.source_ref == self.target_ref:
            raise ValueError("causal edge cannot connect a node to itself")
        if not self.correlation_id.strip():
            raise ValueError("correlation_id is required")
        if not self.evidence_ref.strip():
            raise ValueError("evidence_ref is required")
        if self.authority != "none":
            raise ValueError("causal edges cannot carry authority")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "edge_id": self.edge_id,
            "workspace_id": self.workspace_id,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
            "relation": self.relation.value,
            "correlation_id": self.correlation_id,
            "evidence_ref": self.evidence_ref,
            "created_at": self.created_at.isoformat(),
            "authority": self.authority,
        }


@dataclass(frozen=True)
class IncidentGraph:
    workspace_id: str
    incidents: Tuple[IncidentRecord, ...] = ()
    edges: Tuple[CausalEdge, ...] = ()

    def __post_init__(self):
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        for incident in self.incidents:
            if incident.workspace_id != self.workspace_id:
                raise ValueError("incident crosses workspace boundary")
        for edge in self.edges:
            if edge.workspace_id != self.workspace_id:
                raise ValueError("causal edge crosses workspace boundary")
        incident_ids = {i.incident_id for i in self.incidents}
        for edge in self.edges:
            for ref in (edge.source_ref, edge.target_ref):
                if ref.startswith("incident:") and ref.split(":", 1)[1] not in incident_ids:
                    raise ValueError("graph edge references unknown incident")

    def add_incident(self, incident: IncidentRecord) -> "IncidentGraph":
        if incident.workspace_id != self.workspace_id:
            raise ValueError("incident crosses workspace boundary")
        if any(i.incident_id == incident.incident_id for i in self.incidents):
            return self
        return replace(self, incidents=self.incidents + (incident,))

    def add_edge(self, edge: CausalEdge) -> "IncidentGraph":
        if edge.workspace_id != self.workspace_id:
            raise ValueError("causal edge crosses workspace boundary")
        if any(e.edge_id == edge.edge_id for e in self.edges):
            return self
        return replace(self, edges=self.edges + (edge,))

    def impact_path(self, start_ref: str) -> Tuple[str, ...]:
        """Return a read-only breadth-first impact projection."""
        adjacency = {}
        for edge in self.edges:
            adjacency.setdefault(edge.source_ref, []).append(edge.target_ref)
        seen = {start_ref}
        queue = [start_ref]
        result = []
        while queue:
            current = queue.pop(0)
            for target in adjacency.get(current, []):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
                    result.append(target)
        return tuple(result)
