"""V71.1 — Governed AI Workforce: Agent Identity Plane.

Defines durable, tenant-scoped identity for AI workers. Identity describes who
an agent is, why it exists, what class of worker it is, and which capabilities
it declares. It does NOT grant authority, permissions, execution rights, or
provider access. Those remain separate governance controls.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
import hashlib, json


class AgentStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class AgentBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    INVALID_IDENTITY = "invalid_identity"
    DUPLICATE_AGENT = "duplicate_agent"
    NOT_FOUND = "agent_not_found"
    TERMINAL = "agent_retired"
    INACTIVE = "agent_not_active"
    INVALID_TRANSITION = "invalid_transition"
    IDENTITY_TAMPERED = "identity_tampered"


TERMINAL = frozenset({AgentStatus.RETIRED})
_ALLOWED_TRANSITIONS = {
    AgentStatus.PROPOSED: frozenset({AgentStatus.ACTIVE, AgentStatus.RETIRED}),
    AgentStatus.ACTIVE: frozenset({AgentStatus.SUSPENDED, AgentStatus.RETIRED}),
    AgentStatus.SUSPENDED: frozenset({AgentStatus.ACTIVE, AgentStatus.RETIRED}),
    AgentStatus.RETIRED: frozenset(),
}


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    workspace_id: str
    name: str
    purpose: str
    agent_class: str = "business_worker"
    version: str = "1"
    status: AgentStatus = AgentStatus.PROPOSED
    declared_capabilities: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    identity_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "purpose": self.purpose,
            "agent_class": self.agent_class,
            "version": self.version,
            "status": self.status.value,
            "declared_capabilities": sorted(set(self.declared_capabilities)),
            "metadata": dict(self.metadata),
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

    def with_status(self, status: AgentStatus) -> "AgentIdentity":
        return AgentIdentity(**{**self.__dict__, "status": status, "identity_fingerprint": ""})


class AgentIdentityRegistry:
    VERSION = "v71.1"

    def __init__(self, workspace_id: str):
        ws = str(workspace_id or "").strip()
        if not ws:
            raise ValueError(AgentBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self._agents: dict[str, AgentIdentity] = {}

    def register(self, *, agent_id: str, name: str, purpose: str,
                 agent_class: str = "business_worker", version: str = "1",
                 declared_capabilities: tuple[str, ...] = (),
                 metadata: Optional[Mapping[str, Any]] = None) -> AgentIdentity:
        aid = str(agent_id or "").strip()
        if not aid or not str(name or "").strip() or not str(purpose or "").strip() or not str(agent_class or "").strip() or not str(version or "").strip():
            raise ValueError(AgentBlock.INVALID_IDENTITY.value)
        if aid in self._agents:
            raise ValueError(AgentBlock.DUPLICATE_AGENT.value)
        caps = tuple(sorted({str(x).strip() for x in declared_capabilities if str(x).strip()}))
        identity = AgentIdentity(aid, self.workspace_id, str(name).strip(), str(purpose).strip(),
                                 str(agent_class).strip(), str(version).strip(), AgentStatus.PROPOSED,
                                 caps, dict(metadata or {}), "")
        identity = AgentIdentity(**{**identity.__dict__, "identity_fingerprint": identity.fingerprint()})
        self._agents[aid] = identity
        return identity

    def get(self, agent_id: str) -> Optional[AgentIdentity]:
        return self._agents.get(str(agent_id))

    def require_active(self, agent_id: str) -> AgentIdentity:
        identity = self.get(agent_id)
        if identity is None:
            raise ValueError(AgentBlock.NOT_FOUND.value)
        self.assert_integrity(identity)
        if identity.status == AgentStatus.RETIRED:
            raise ValueError(AgentBlock.TERMINAL.value)
        if identity.status != AgentStatus.ACTIVE:
            raise ValueError(AgentBlock.INACTIVE.value)
        return identity

    def transition(self, *, agent_id: str, status: AgentStatus) -> AgentIdentity:
        current = self.get(agent_id)
        if current is None:
            raise ValueError(AgentBlock.NOT_FOUND.value)
        self.assert_integrity(current)
        status = AgentStatus(status)
        if status not in _ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(AgentBlock.INVALID_TRANSITION.value)
        updated = current.with_status(status)
        updated = AgentIdentity(**{**updated.__dict__, "identity_fingerprint": updated.fingerprint()})
        self._agents[agent_id] = updated
        return updated

    def assert_integrity(self, identity: AgentIdentity) -> None:
        if identity.workspace_id != self.workspace_id:
            raise ValueError(AgentBlock.TENANT_MISMATCH.value)
        if identity.identity_fingerprint != identity.fingerprint():
            raise ValueError(AgentBlock.IDENTITY_TAMPERED.value)

    def list_agents(self) -> tuple[AgentIdentity, ...]:
        return tuple(self._agents.values())

    def can_execute(self, agent_id: str) -> bool:
        """Identity is never authority: active only means eligible for later governance checks."""
        try:
            self.require_active(agent_id)
            return True
        except ValueError:
            return False


__all__ = ["AgentStatus", "AgentBlock", "AgentIdentity", "AgentIdentityRegistry"]
