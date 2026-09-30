"""Digital immune response: detect, contain, remember, recover.

This layer produces bounded incident classifications and containment proposals.
It does not disable infrastructure, change credentials, or execute remediation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Tuple

from core.organism_state import OrganismState


class ThreatClass(str, Enum):
    NONE = "none"
    ANOMALY = "anomaly"
    DEPENDENCY_FAILURE = "dependency_failure"
    SECURITY_DEGRADATION = "security_degradation"
    EXECUTION_UNCERTAINTY = "execution_uncertainty"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


class ImmuneAction(str, Enum):
    OBSERVE = "observe"
    CONTAIN = "contain"
    QUARANTINE = "quarantine"
    ESCALATE = "escalate"
    RECOVER = "recover"


@dataclass(frozen=True)
class ImmuneSignal:
    workspace_id: str
    threat: ThreatClass
    severity: float
    action: ImmuneAction
    reason: str
    fingerprint: str
    evidence: Mapping[str, object]


def detect_immune_signals(state: OrganismState) -> Tuple[ImmuneSignal, ...]:
    signals: list[ImmuneSignal] = []
    if not state.topology_healthy:
        signals.append(_signal(state, ThreatClass.DEPENDENCY_FAILURE, 0.9, ImmuneAction.CONTAIN, "topology/dependency failure"))
    if state.security_posture < 0.35:
        signals.append(_signal(state, ThreatClass.SECURITY_DEGRADATION, 1.0, ImmuneAction.QUARANTINE, "security posture below safe threshold"))
    if state.resource_reserve < 0.15:
        signals.append(_signal(state, ThreatClass.RESOURCE_EXHAUSTION, 0.9, ImmuneAction.CONTAIN, "resource reserve critically low"))
    if state.operational_load > 0.95:
        signals.append(_signal(state, ThreatClass.ANOMALY, 0.8, ImmuneAction.CONTAIN, "operational load critically high"))
    if state.risk > 0.8 and state.confidence.value == "low":
        signals.append(_signal(state, ThreatClass.ANOMALY, 0.85, ImmuneAction.ESCALATE, "high risk with low confidence"))
    return tuple(sorted(signals, key=lambda s: s.severity, reverse=True))


def _signal(state: OrganismState, threat: ThreatClass, severity: float, action: ImmuneAction, reason: str) -> ImmuneSignal:
    fingerprint = f"{threat.value}:{state.workspace_id}:{reason}"
    return ImmuneSignal(state.workspace_id, threat, severity, action, reason, fingerprint, {
        "mode": state.mode.value,
        "risk": state.risk,
        "security_posture": state.security_posture,
        "resource_reserve": state.resource_reserve,
        "topology_healthy": state.topology_healthy,
    })
