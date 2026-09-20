"""Bounded multi-organism coordination without creating a new authority plane.

An ecosystem is an explicit opt-in exchange boundary. Organisms exchange only
redacted, declarative signals; no credentials, raw tenant data, executable
handlers, or execution authority cross that boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping


class SignalKind(str, Enum):
    OPPORTUNITY = "opportunity"
    CAPACITY = "capacity"
    DEPENDENCY = "dependency"
    RISK = "risk"
    RESOURCE = "resource"
    LEARNING = "learning"


class CoordinationStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class OrganismSignal:
    """A deliberately small, non-authoritative message between organisms."""

    ecosystem_id: str
    source_workspace_id: str
    target_workspace_id: str
    kind: SignalKind
    subject: str
    value: str
    confidence: float = 0.5
    expires_at: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.ecosystem_id.strip() or not self.source_workspace_id.strip() or not self.target_workspace_id.strip():
            raise ValueError("ecosystem and workspace identifiers are required")
        if self.source_workspace_id == self.target_workspace_id:
            raise ValueError("ecosystem signals must target another organism")
        if not self.subject.strip() or not self.value.strip():
            raise ValueError("signal subject and value are required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.created_at.tzinfo is None or (self.expires_at is not None and self.expires_at.tzinfo is None):
            raise ValueError("timestamps must be timezone-aware")
        forbidden = {"credential", "credentials", "secret", "token", "password", "permission", "permissions", "authority", "code", "handler"}
        if forbidden.intersection(str(k).lower() for k in self.metadata):
            raise ValueError("signal metadata cannot carry credentials, authority, or executable code")
        if len(self.subject) > 160 or len(self.value) > 1000 or len(self.metadata) > 16:
            raise ValueError("signal exceeds ecosystem bounds")


@dataclass(frozen=True)
class CoordinationProposal:
    ecosystem_id: str
    signal: OrganismSignal
    status: CoordinationStatus = CoordinationStatus.PROPOSED
    reason: str = ""


@dataclass(frozen=True)
class EcosystemPolicy:
    """Explicit opt-in membership and signal boundary."""

    ecosystem_id: str
    members: frozenset[str]
    allowed_kinds: frozenset[SignalKind] = frozenset(SignalKind)
    max_signals: int = 512

    def __post_init__(self) -> None:
        if not self.ecosystem_id.strip() or not self.members:
            raise ValueError("ecosystem_id and members are required")
        if len(self.members) > 128:
            raise ValueError("ecosystem member limit exceeded")
        if self.max_signals < 1 or self.max_signals > 10_000:
            raise ValueError("invalid ecosystem signal limit")


class EcosystemCoordinator:
    """Coordinates declarative signals; it never authorizes or executes actions."""

    def __init__(self) -> None:
        self._policies: dict[str, EcosystemPolicy] = {}
        self._signals: dict[str, list[OrganismSignal]] = {}

    def register(self, policy: EcosystemPolicy) -> EcosystemPolicy:
        existing = self._policies.get(policy.ecosystem_id)
        if existing is not None and not policy.members.issuperset(existing.members):
            raise ValueError("ecosystem membership cannot silently remove existing members")
        self._policies[policy.ecosystem_id] = policy
        self._signals.setdefault(policy.ecosystem_id, [])
        return policy

    def propose(self, signal: OrganismSignal) -> CoordinationProposal:
        policy = self._policies.get(signal.ecosystem_id)
        if policy is None:
            return CoordinationProposal(signal.ecosystem_id, signal, CoordinationStatus.REJECTED, "ecosystem is not registered")
        if signal.source_workspace_id not in policy.members or signal.target_workspace_id not in policy.members:
            return CoordinationProposal(signal.ecosystem_id, signal, CoordinationStatus.REJECTED, "source or target is not an ecosystem member")
        if signal.kind not in policy.allowed_kinds:
            return CoordinationProposal(signal.ecosystem_id, signal, CoordinationStatus.REJECTED, "signal kind is not permitted")
        bucket = self._signals[signal.ecosystem_id]
        if len(bucket) >= policy.max_signals:
            return CoordinationProposal(signal.ecosystem_id, signal, CoordinationStatus.REJECTED, "ecosystem signal limit exceeded")
        bucket.append(signal)
        return CoordinationProposal(signal.ecosystem_id, signal, CoordinationStatus.ACCEPTED, "signal accepted for bounded coordination")

    def signals_for(self, ecosystem_id: str, workspace_id: str, *, now: datetime | None = None) -> tuple[OrganismSignal, ...]:
        policy = self._policies.get(ecosystem_id)
        if policy is None or workspace_id not in policy.members:
            raise PermissionError("workspace is not an ecosystem member")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return tuple(
            signal for signal in self._signals.get(ecosystem_id, ())
            if signal.target_workspace_id == workspace_id and (signal.expires_at is None or signal.expires_at > current)
        )

    def status(self, ecosystem_id: str) -> Mapping[str, int]:
        policy = self._policies.get(ecosystem_id)
        if policy is None:
            return {"members": 0, "signals": 0}
        return {"members": len(policy.members), "signals": len(self._signals.get(ecosystem_id, ()))}
