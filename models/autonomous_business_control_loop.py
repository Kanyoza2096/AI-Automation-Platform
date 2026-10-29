"""Phase 9 — bounded autonomous business control-loop contracts.

This module is a read/admission model over KDO's existing autonomy envelope,
decision, reliability, provenance, governance and Action Kernel boundaries.
It never executes or authorizes an action.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json


class LoopMode(str, Enum):
    OBSERVE = "observe"
    SUGGEST = "suggest"
    GOVERNED_AUTONOMY = "governed_autonomy"
    HUMAN_ONLY = "human_only"
    PAUSED = "paused"


class LoopDisposition(str, Enum):
    OBSERVE = "observe"
    PROPOSE = "propose"
    ESCALATE = "escalate"
    BLOCK = "block"
    READY_FOR_EXISTING_GOVERNANCE = "ready_for_existing_governance"


@dataclass(frozen=True)
class ControlLoopObservation:
    workspace_id: str
    signal_id: str
    state: str
    confidence: float = 0.0
    drift_detected: bool = False
    evidence_ids: tuple[str, ...] = ()
    source: str = "unknown"

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.signal_id.strip():
            raise ValueError("workspace_id and signal_id are required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class ControlLoopCycle:
    workspace_id: str
    loop_id: str
    observation_ids: tuple[str, ...]
    decision_id: Optional[str]
    proposed_action: Optional[str]
    disposition: LoopDisposition
    reason_codes: tuple[str, ...] = ()
    cooldown_until: Optional[str] = None
    human_takeover_required: bool = False
    authority: str = "none"
    execution_requested: bool = False
    action_intent_created: bool = False

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.loop_id.strip():
            raise ValueError("workspace_id and loop_id are required")
        if self.authority != "none":
            raise ValueError("control loop cannot grant authority")
        if self.execution_requested or self.action_intent_created:
            raise ValueError("control loop cannot execute or create ActionIntents")


@dataclass(frozen=True)
class AutonomousBusinessControlLoop:
    workspace_id: str
    loop_id: str
    objective: str
    mode: LoopMode = LoopMode.SUGGEST
    allowed_actions: tuple[str, ...] = ()
    cooldown_seconds: int = 0
    max_consecutive_failures: int = 3
    human_takeover_on_failure: bool = True
    enabled: bool = False
    version: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    authority: str = field(default="none", init=False)
    execution_effect: str = field(default="none", init=False)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.loop_id.strip() or not self.objective.strip():
            raise ValueError("workspace_id, loop_id and objective are required")
        if self.cooldown_seconds < 0 or self.max_consecutive_failures < 1:
            raise ValueError("invalid loop limits")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")
        if self.mode == LoopMode.GOVERNED_AUTONOMY and not self.allowed_actions:
            raise ValueError("governed autonomy requires an explicit action allowlist")
        if self.authority != "none" or self.execution_effect != "none":
            raise ValueError("loop cannot grant authority or execution effects")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "loop_id": self.loop_id,
            "objective": self.objective,
            "mode": self.mode.value,
            "allowed_actions": list(self.allowed_actions),
            "cooldown_seconds": self.cooldown_seconds,
            "max_consecutive_failures": self.max_consecutive_failures,
            "human_takeover_on_failure": self.human_takeover_on_failure,
            "enabled": self.enabled,
            "version": self.version,
            "metadata": _canonicalize(self.metadata),
            "authority": "none",
            "execution_effect": "none",
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        out = self.canonical_dict(); out["fingerprint"] = self.fingerprint(); return out


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(value[k]) for k in sorted(value, key=lambda x: str(x))}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value
