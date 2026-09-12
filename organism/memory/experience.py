"""Tenant-scoped organism memory and experience contracts."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Mapping, Optional
import uuid

class MemoryKind(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"

class Outcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    UNCERTAIN = "uncertain"

@dataclass(frozen=True)
class Memory:
    workspace_id: str
    kind: MemoryKind
    content: Mapping[str, Any]
    source: str = "internal"
    confidence: float = 1.0
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    memory_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:16]}")
    tags: tuple[str, ...] = ()
    expires_at: Optional[datetime] = None
    def __post_init__(self) -> None:
        if not str(self.workspace_id).strip() or not str(self.source).strip(): raise ValueError("workspace_id and source are required")
        if not 0.0 <= float(self.confidence) <= 1.0: raise ValueError("confidence must be between 0 and 1")
        if self.occurred_at.tzinfo is None: raise ValueError("occurred_at must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None: raise ValueError("expires_at must be timezone-aware")
    def is_expired(self, now: Optional[datetime] = None) -> bool:
        return self.expires_at is not None and (now or datetime.now(timezone.utc)) >= self.expires_at
    def to_dict(self) -> dict[str, Any]:
        return {"workspace_id": self.workspace_id, "kind": self.kind.value, "content": dict(self.content), "source": self.source, "confidence": self.confidence, "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(), "memory_id": self.memory_id, "tags": list(self.tags), "expires_at": self.expires_at.astimezone(timezone.utc).isoformat() if self.expires_at else None}

@dataclass(frozen=True)
class Experience:
    workspace_id: str
    goal_id: Optional[str]
    action: str
    outcome: Outcome
    result: Mapping[str, Any] = field(default_factory=dict)
    lesson: Optional[str] = None
    confidence: float = 1.0
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    experience_id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:16]}")
    def __post_init__(self) -> None:
        if not str(self.workspace_id).strip() or not str(self.action).strip(): raise ValueError("workspace_id and action are required")
        if not 0.0 <= float(self.confidence) <= 1.0: raise ValueError("confidence must be between 0 and 1")
        if self.occurred_at.tzinfo is None: raise ValueError("occurred_at must be timezone-aware")
    def to_memory(self) -> Memory:
        return Memory(self.workspace_id, MemoryKind.EPISODIC, {"experience_id": self.experience_id, "goal_id": self.goal_id, "action": self.action, "outcome": self.outcome.value, "result": dict(self.result), "lesson": self.lesson}, source="experience", confidence=self.confidence, occurred_at=self.occurred_at, tags=("experience", self.outcome.value))

class MemoryStore:
    def __init__(self, *, max_memories_per_workspace: int = 5000) -> None:
        if max_memories_per_workspace < 1: raise ValueError("max_memories_per_workspace must be positive")
        self._limit = max_memories_per_workspace; self._items: dict[str, list[Memory]] = {}; self._lock = RLock()
    def remember(self, memory: Memory) -> Memory:
        with self._lock:
            items = self._items.setdefault(memory.workspace_id, []); items.append(memory)
            if len(items) > self._limit: del items[:len(items) - self._limit]
        return memory
    def record_experience(self, experience: Experience) -> Memory:
        """Record an experience exactly once when it represents a durable execution."""
        candidate = experience.to_memory()
        execution_id = candidate.content.get("result", {}).get("execution_id")
        with self._lock:
            if execution_id:
                for item in reversed(self._items.get(experience.workspace_id, ())):
                    if item.kind is MemoryKind.EPISODIC and item.content.get("result", {}).get("execution_id") == execution_id:
                        return item
        return self.remember(candidate)
    def recent(self, workspace_id: str, *, kind: Optional[MemoryKind] = None, tags: Optional[set[str]] = None, limit: int = 100) -> tuple[Memory, ...]:
        if limit < 1: raise ValueError("limit must be positive")
        with self._lock: items = list(self._items.get(workspace_id, ()))
        if kind is not None: items = [i for i in items if i.kind == kind]
        if tags: items = [i for i in items if tags.intersection(i.tags)]
        return tuple(items[-limit:])
    def status(self, workspace_id: str) -> dict[str, Any]:
        with self._lock: items = tuple(self._items.get(workspace_id, ()))
        return {"workspace_id": workspace_id, "memories": len(items), "episodic": sum(i.kind == MemoryKind.EPISODIC for i in items), "semantic": sum(i.kind == MemoryKind.SEMANTIC for i in items), "procedural": sum(i.kind == MemoryKind.PROCEDURAL for i in items)}
