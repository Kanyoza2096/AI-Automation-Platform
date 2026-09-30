"""Typed models for the Reliability Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


class AutonomyLevel(str, Enum):
    """Policy levels — higher = more aggressive automation."""

    OBSERVE = "0_observe"           # collect only
    RECOMMEND = "1_recommend"       # diagnose + plan, no execute
    REVERSIBLE = "2_reversible"     # restart, retry, requeue, refresh
    PLAYBOOK = "3_playbook"         # approved playbooks only
    PATCH_PROPOSE = "4_patch"       # generate patch → human approval
    RESTRICTED_DEPLOY = "5_deploy"  # highly restricted (default OFF)


class IncidentSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RemediationAction(str, Enum):
    """Catalog of allowed remediation actions (typed playbook — not arbitrary code)."""

    NOOP = "noop"
    OBSERVE_ONLY = "observe_only"
    RETRY_OPERATION = "retry_operation"
    REQUEUE_OUTBOX = "requeue_outbox"
    RESTART_WORKER = "restart_worker"
    REFRESH_CONNECTION = "refresh_connection"
    DISABLE_CONNECTOR = "disable_connector"
    CLEAR_STALE_STATE = "clear_stale_state"
    RECONCILE_HEALTH_SCOPE = "reconcile_health_scope"
    ROLLBACK_RELEASE = "rollback_release"
    ESCALATE_HUMAN = "escalate_human"
    PROPOSE_PATCH = "propose_patch"  # Level 4 only — never auto-applied


# Actions that Level 2 may execute without human approval when policy allows
REVERSIBLE_ACTIONS = frozenset({
    RemediationAction.NOOP,
    RemediationAction.OBSERVE_ONLY,
    RemediationAction.RETRY_OPERATION,
    RemediationAction.REQUEUE_OUTBOX,
    RemediationAction.RESTART_WORKER,
    RemediationAction.REFRESH_CONNECTION,
    RemediationAction.CLEAR_STALE_STATE,
    RemediationAction.RECONCILE_HEALTH_SCOPE,
})


@dataclass
class Incident:
    id: str = field(default_factory=lambda: f"inc_{uuid.uuid4().hex[:12]}")
    symptom: str = ""
    source: str = "unknown"
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    component: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_iso)
    workspace_id: str = "platform"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symptom": self.symptom,
            "source": self.source,
            "severity": self.severity.value,
            "component": self.component,
            "evidence": self.evidence,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "workspace_id": self.workspace_id,
        }


@dataclass
class Diagnosis:
    incident_id: str
    probable_cause: str
    confidence: float
    evidence_summary: List[str] = field(default_factory=list)
    related_components: List[str] = field(default_factory=list)
    similar_incident_ids: List[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "probable_cause": self.probable_cause,
            "confidence": round(self.confidence, 3),
            "evidence_summary": self.evidence_summary,
            "related_components": self.related_components,
            "similar_incident_ids": self.similar_incident_ids,
            "notes": self.notes,
        }


@dataclass
class RemediationPlan:
    id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:10]}")
    incident_id: str = ""
    action: RemediationAction = RemediationAction.OBSERVE_ONLY
    risk: str = "low"  # low | medium | high | critical
    confidence: float = 0.0
    reason: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    requires_human: bool = False
    autonomy_required: AutonomyLevel = AutonomyLevel.RECOMMEND

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "action": self.action.value,
            "risk": self.risk,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "params": self.params,
            "requires_human": self.requires_human,
            "autonomy_required": self.autonomy_required.value,
        }


@dataclass
class RemediationResult:
    plan_id: str
    incident_id: str
    status: str  # success | failed | skipped | denied | escalated
    detail: Dict[str, Any] = field(default_factory=dict)
    validated: Optional[bool] = None
    completed_at: str = field(default_factory=_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "incident_id": self.incident_id,
            "status": self.status,
            "detail": self.detail,
            "validated": self.validated,
            "completed_at": self.completed_at,
        }
