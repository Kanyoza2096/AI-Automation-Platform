"""V71.3 — Governed AI Workforce: Agent Memory Plane.

Memory gives an AI worker bounded context; it never grants authority. Memory is
workspace-scoped, provenance-aware, integrity-protected and explicitly advisory.
Sensitive/expired/revoked/conflicting memories are not silently promoted to fact.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional
from datetime import datetime, timezone
import hashlib, json

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus


class MemoryKind(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    OBSERVATION = "observation"
    LESSON = "lesson"
    DECISION_CONTEXT = "decision_context"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class MemoryBlock(str, Enum):
    TENANT_MISMATCH = "tenant_mismatch"
    AGENT_NOT_FOUND = "agent_not_found"
    AGENT_NOT_ACTIVE = "agent_not_active"
    INVALID_MEMORY = "invalid_memory"
    DUPLICATE_MEMORY = "duplicate_memory"
    MEMORY_TAMPERED = "memory_tampered"
    MEMORY_NOT_FOUND = "memory_not_found"
    MEMORY_UNAVAILABLE = "memory_unavailable"
    CONFLICT = "memory_conflict"


@dataclass(frozen=True)
class AgentMemory:
    memory_id: str
    workspace_id: str
    agent_id: str
    kind: MemoryKind
    subject: str
    content: str
    source: str
    confidence: float = 1.0
    created_at: str = ""
    expires_at: Optional[str] = None
    sensitivity: str = "normal"
    status: MemoryStatus = MemoryStatus.ACTIVE
    supersedes_memory_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    memory_fingerprint: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id, "workspace_id": self.workspace_id,
            "agent_id": self.agent_id, "kind": self.kind.value,
            "subject": self.subject, "content": self.content, "source": self.source,
            "confidence": self.confidence, "created_at": self.created_at,
            "expires_at": self.expires_at, "sensitivity": self.sensitivity,
            "status": self.status.value, "supersedes_memory_id": self.supersedes_memory_id,
            "metadata": dict(self.metadata),
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return current >= expiry
        except ValueError:
            return True


@dataclass(frozen=True)
class MemoryRecall:
    memory: AgentMemory
    advisory: bool = True
    conflict_ids: tuple[str, ...] = ()


class AgentMemoryRegistry:
    VERSION = "v71.3"

    def __init__(self, workspace_id: str, identity_registry: AgentIdentityRegistry):
        ws = str(workspace_id or "").strip()
        if not ws or identity_registry.workspace_id != ws:
            raise ValueError(MemoryBlock.TENANT_MISMATCH.value)
        self.workspace_id = ws
        self.identity_registry = identity_registry
        self._memories: dict[str, AgentMemory] = {}

    def _validate_agent(self, agent_id: str, require_active: bool = True):
        agent = self.identity_registry.get(agent_id)
        if agent is None:
            raise ValueError(MemoryBlock.AGENT_NOT_FOUND.value)
        self.identity_registry.assert_integrity(agent)
        if require_active and agent.status != AgentStatus.ACTIVE:
            raise ValueError(MemoryBlock.AGENT_NOT_ACTIVE.value)
        return agent

    def remember(self, *, memory_id: str, agent_id: str, kind: MemoryKind,
                 subject: str, content: str, source: str, confidence: float = 1.0,
                 expires_at: Optional[str] = None, sensitivity: str = "normal",
                 supersedes_memory_id: Optional[str] = None,
                 metadata: Optional[Mapping[str, Any]] = None) -> AgentMemory:
        mid = str(memory_id or "").strip()
        subject, content, source = str(subject or "").strip(), str(content or "").strip(), str(source or "").strip()
        if not mid or not subject or not content or not source or not str(sensitivity or "").strip():
            raise ValueError(MemoryBlock.INVALID_MEMORY.value)
        if mid in self._memories:
            raise ValueError(MemoryBlock.DUPLICATE_MEMORY.value)
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            raise ValueError(MemoryBlock.INVALID_MEMORY.value)
        if not 0.0 <= conf <= 1.0:
            raise ValueError(MemoryBlock.INVALID_MEMORY.value)
        self._validate_agent(agent_id)
        now = datetime.now(timezone.utc).isoformat()
        memory = AgentMemory(mid, self.workspace_id, str(agent_id), MemoryKind(kind), subject,
                             content, source, conf, now, expires_at, str(sensitivity).strip(),
                             MemoryStatus.ACTIVE, supersedes_memory_id, dict(metadata or {}), "")
        memory = AgentMemory(**{**memory.__dict__, "memory_fingerprint": memory.fingerprint()})
        self._memories[mid] = memory
        return memory

    def get(self, memory_id: str) -> Optional[AgentMemory]:
        return self._memories.get(str(memory_id))

    def assert_integrity(self, memory: AgentMemory) -> None:
        if memory.workspace_id != self.workspace_id:
            raise ValueError(MemoryBlock.TENANT_MISMATCH.value)
        if memory.memory_fingerprint != memory.fingerprint():
            raise ValueError(MemoryBlock.MEMORY_TAMPERED.value)

    def revoke(self, memory_id: str) -> AgentMemory:
        memory = self.get(memory_id)
        if memory is None:
            raise ValueError(MemoryBlock.MEMORY_NOT_FOUND.value)
        self.assert_integrity(memory)
        updated = AgentMemory(**{**memory.__dict__, "status": MemoryStatus.REVOKED, "memory_fingerprint": ""})
        updated = AgentMemory(**{**updated.__dict__, "memory_fingerprint": updated.fingerprint()})
        self._memories[memory.memory_id] = updated
        return updated

    def recall(self, *, agent_id: str, subject: Optional[str] = None,
               kind: Optional[MemoryKind] = None, include_conflicts: bool = False,
               now: Optional[datetime] = None) -> tuple[MemoryRecall, ...]:
        self._validate_agent(agent_id)
        rows = []
        for memory in self._memories.values():
            if memory.agent_id != str(agent_id):
                continue
            self.assert_integrity(memory)
            if memory.status != MemoryStatus.ACTIVE or memory.is_expired(now):
                continue
            if subject is not None and memory.subject != str(subject):
                continue
            if kind is not None and memory.kind != MemoryKind(kind):
                continue
            rows.append(memory)
        results = []
        for memory in rows:
            conflicts = tuple(sorted(m.memory_id for m in rows
                                     if m.memory_id != memory.memory_id
                                     and m.subject == memory.subject
                                     and m.kind == memory.kind
                                     and m.content != memory.content))
            if conflicts and not include_conflicts:
                continue
            results.append(MemoryRecall(memory, advisory=True, conflict_ids=conflicts))
        return tuple(results)

    def list_memories(self, agent_id: Optional[str] = None) -> tuple[AgentMemory, ...]:
        values = tuple(self._memories.values())
        if agent_id is None:
            return values
        return tuple(m for m in values if m.agent_id == str(agent_id))

    def usable_context(self, *, agent_id: str, subject: Optional[str] = None) -> tuple[Mapping[str, Any], ...]:
        """Return context only; this method intentionally exposes no action/authority fields."""
        return tuple({"memory_id": r.memory.memory_id, "kind": r.memory.kind.value,
                      "subject": r.memory.subject, "content": r.memory.content,
                      "source": r.memory.source, "confidence": r.memory.confidence,
                      "advisory": True, "conflict_ids": r.conflict_ids}
                     for r in self.recall(agent_id=agent_id, subject=subject))


__all__ = ["MemoryKind", "MemoryStatus", "MemoryBlock", "AgentMemory", "MemoryRecall", "AgentMemoryRegistry"]
