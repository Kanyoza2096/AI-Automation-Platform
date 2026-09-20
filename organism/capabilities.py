"""Governed capability registry for the digital organism.

Capabilities are declarative descriptions of what the platform can *ask* the
existing execution/governance planes to do.  This registry never contains
credentials, executable strategy code, or an authority-granting mechanism.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Mapping


class CapabilityStatus(str, Enum):
    ENABLED = "enabled"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"


class CapabilityRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuthorityRequirement(str, Enum):
    SUGGEST = "suggest"
    EXECUTE = "execute"
    PUBLISH = "publish"
    HUMAN = "human"


@dataclass(frozen=True)
class CapabilitySpec:
    """Immutable, tenant-scoped capability contract."""

    workspace_id: str
    name: str
    version: str
    description: str
    risk: CapabilityRisk = CapabilityRisk.MEDIUM
    authority: AuthorityRequirement = AuthorityRequirement.SUGGEST
    prerequisites: tuple[str, ...] = ()
    status: CapabilityStatus = CapabilityStatus.ENABLED
    reversible: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.name.strip() or not self.version.strip():
            raise ValueError("workspace_id, name and version are required")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if any(not item.strip() for item in self.prerequisites):
            raise ValueError("prerequisites must be non-empty names")
        if self.name in self.prerequisites:
            raise ValueError("capability cannot depend on itself")
        # Capability metadata is descriptive only; reject obvious authority/code channels.
        forbidden = {"credential", "credentials", "secret", "token", "permission", "permissions", "handler", "code"}
        if forbidden.intersection(str(k).lower() for k in self.metadata):
            raise ValueError("capability metadata cannot contain authority, credential, or executable-code fields")


@dataclass(frozen=True)
class CapabilityHealth:
    name: str
    workspace_id: str
    status: CapabilityStatus
    score: float
    reason: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.score) <= 1.0:
            raise ValueError("health score must be between 0 and 1")


@dataclass(frozen=True)
class CapabilityCheck:
    workspace_id: str
    capability: str
    allowed: bool
    reason: str
    missing_prerequisites: tuple[str, ...] = ()
    required_authority: AuthorityRequirement = AuthorityRequirement.SUGGEST


class CapabilityRegistry:
    """Bounded per-workspace registry; no execution handlers are stored here."""

    MAX_CAPABILITIES_PER_WORKSPACE = 256
    _authority_levels = {"suggest": 0, "execute": 1, "publish": 2, "human": 3}

    def __init__(self) -> None:
        self._items: dict[str, dict[str, CapabilitySpec]] = {}
        self._health: dict[tuple[str, str], CapabilityHealth] = {}

    def register(self, capability: CapabilitySpec) -> CapabilitySpec:
        bucket = self._items.setdefault(capability.workspace_id, {})
        existing = bucket.get(capability.name)
        if existing is not None and existing.version == capability.version:
            return existing
        if existing is not None and self._version_key(capability.version) < self._version_key(existing.version):
            raise ValueError("capability version cannot move backwards")
        if existing is None and len(bucket) >= self.MAX_CAPABILITIES_PER_WORKSPACE:
            raise ValueError("workspace capability limit exceeded")
        bucket[capability.name] = capability
        return capability

    def get(self, workspace_id: str, name: str) -> CapabilitySpec | None:
        return self._items.get(workspace_id, {}).get(name)

    def list(self, workspace_id: str) -> tuple[CapabilitySpec, ...]:
        return tuple(self._items.get(workspace_id, {}).values())

    def set_health(self, health: CapabilityHealth) -> CapabilityHealth:
        if self.get(health.workspace_id, health.name) is None:
            raise KeyError("unknown capability")
        self._health[(health.workspace_id, health.name)] = health
        return health

    def check(
        self,
        workspace_id: str,
        name: str,
        *,
        authority: str = "suggest",
        available: Iterable[str] = (),
    ) -> CapabilityCheck:
        spec = self.get(workspace_id, name)
        if spec is None:
            return CapabilityCheck(workspace_id, name, False, "capability is not registered")
        if spec.status is not CapabilityStatus.ENABLED:
            return CapabilityCheck(workspace_id, name, False, f"capability status is '{spec.status.value}'", required_authority=spec.authority)
        health = self._health.get((workspace_id, name))
        if health is not None and health.status in {CapabilityStatus.DISABLED, CapabilityStatus.QUARANTINED}:
            return CapabilityCheck(workspace_id, name, False, f"capability health is '{health.status.value}'", required_authority=spec.authority)
        available_set = set(available)
        missing = tuple(p for p in spec.prerequisites if p not in available_set)
        if missing:
            return CapabilityCheck(workspace_id, name, False, "capability prerequisites are unavailable", missing, spec.authority)
        if self._authority_levels.get(authority, -1) < self._authority_levels[spec.authority.value]:
            return CapabilityCheck(workspace_id, name, False, f"authority '{authority}' is below required '{spec.authority.value}'", (), spec.authority)
        return CapabilityCheck(workspace_id, name, True, "capability is available", (), spec.authority)

    @staticmethod
    def _version_key(version: str) -> tuple[int, int, int]:
        raw = version.lstrip("v").split(".")
        values: list[int] = []
        for part in raw[:3]:
            digits = "".join(ch for ch in part if ch.isdigit())
            values.append(int(digits or 0))
        return tuple(values + [0] * (3 - len(values)))
