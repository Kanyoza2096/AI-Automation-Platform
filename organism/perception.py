"""Bounded perception contracts for the Kanyoza organism.

Perception normalizes observations from existing authoritative subsystems. It
never grants authority and never performs external side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional
import uuid


class ObservationKind(str, Enum):
    HEALTH = "health"
    EXECUTION = "execution"
    RESOURCE = "resource"
    SECURITY = "security"
    GOAL = "goal"
    LEARNING = "learning"
    EXTERNAL = "external"


@dataclass(frozen=True)
class Observation:
    workspace_id: str
    kind: ObservationKind
    source: str
    value: Any
    confidence: float = 1.0
    source_trust: float = 1.0
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    observation_id: str = field(default_factory=lambda: f"obs_{uuid.uuid4().hex[:16]}")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.workspace_id).strip() or not str(self.source).strip():
            raise ValueError("workspace_id and source are required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= float(self.source_trust) <= 1.0:
            raise ValueError("source_trust must be between 0 and 1")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "kind": self.kind.value,
            "source": self.source,
            "value": self.value,
            "confidence": self.confidence,
            "source_trust": self.source_trust,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "observation_id": self.observation_id,
            "metadata": dict(self.metadata),
        }


class PerceptionBuffer:
    """Small bounded, tenant-scoped observation buffer."""

    def __init__(self, *, max_observations_per_workspace: int = 1000) -> None:
        if max_observations_per_workspace < 1:
            raise ValueError("max_observations_per_workspace must be positive")
        self._limit = max_observations_per_workspace
        self._items: dict[str, list[Observation]] = {}

    def ingest(self, observation: Observation) -> Observation:
        items = self._items.setdefault(observation.workspace_id, [])
        items.append(observation)
        if len(items) > self._limit:
            del items[: len(items) - self._limit]
        return observation

    def recent(self, workspace_id: str, *, kind: Optional[ObservationKind] = None) -> tuple[Observation, ...]:
        items = self._items.get(workspace_id, [])
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        return tuple(items)
