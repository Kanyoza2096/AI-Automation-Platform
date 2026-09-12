"""Canonical derived state contract for a tenant-scoped Kanyoza organism.

This is deliberately a small, dependency-free truth-plane object. It does not
replace authoritative subsystem records; it provides one bounded snapshot for
supervision, planning, reporting and UI consumers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OperatingMode(str, Enum):
    IDLE = "idle"
    OBSERVING = "observing"
    PLANNING = "planning"
    EXECUTING = "executing"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    STOPPED = "stopped"


@dataclass(frozen=True)
class OrganismState:
    """Bounded, serializable snapshot of one workspace organism."""

    workspace_id: str
    mode: OperatingMode = OperatingMode.IDLE
    health: float = 1.0
    cognitive_load: float = 0.0
    operational_load: float = 0.0
    security_posture: float = 1.0
    resource_reserve: float = 1.0
    goal_progress: float = 0.0
    confidence: Confidence = Confidence.MEDIUM
    risk: float = 0.0
    learning_velocity: float = 0.0
    adaptation: float = 0.0
    objective: Optional[str] = None
    predicted_outcome: Optional[str] = None
    concern: Optional[str] = None
    recommended_action: Optional[str] = None
    authority: str = "suggest"
    topology_healthy: bool = True
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id or not str(self.workspace_id).strip():
            raise ValueError("workspace_id is required")
        for name in (
            "health", "cognitive_load", "operational_load", "security_posture",
            "resource_reserve", "goal_progress", "risk", "learning_velocity",
            "adaptation",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

    def to_dict(self) -> Dict[str, Any]:
        """Return JSON-friendly state without leaking mutable internals."""
        data = asdict(self)
        data["mode"] = self.mode.value
        data["confidence"] = self.confidence.value
        data["updated_at"] = self.updated_at.astimezone(timezone.utc).isoformat()
        data["source_versions"] = dict(self.source_versions)
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "OrganismState":
        """Construct a state snapshot from trusted internal mapping data."""
        values = dict(data)
        if "mode" in values and not isinstance(values["mode"], OperatingMode):
            values["mode"] = OperatingMode(str(values["mode"]))
        if "confidence" in values and not isinstance(values["confidence"], Confidence):
            values["confidence"] = Confidence(str(values["confidence"]))
        if isinstance(values.get("updated_at"), str):
            values["updated_at"] = datetime.fromisoformat(values["updated_at"])
        return cls(**values)
