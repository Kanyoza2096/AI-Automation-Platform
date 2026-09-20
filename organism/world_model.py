"""Tenant-scoped world model foundation for the Kanyoza organism.

The world model is a bounded *derived* knowledge layer.  It never becomes the
source of truth for external side effects or security authority.  Facts carry
provenance, confidence and freshness so planners can distinguish what is
known from what is inferred or stale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from threading import RLock
from typing import Any, Dict, Mapping, Optional
import uuid


class BeliefStatus(str, Enum):
    KNOWN = "known"
    INFERRED = "inferred"
    PREDICTED = "predicted"
    UNCERTAIN = "uncertain"
    CONTRADICTED = "contradicted"
    STALE = "stale"


@dataclass(frozen=True)
class WorldFact:
    """One tenant-scoped observation/belief with explicit provenance."""

    workspace_id: str
    subject: str
    predicate: str
    value: Any
    status: BeliefStatus = BeliefStatus.KNOWN
    confidence: float = 1.0
    source: str = "internal"
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    fact_id: str = field(default_factory=lambda: f"fact_{uuid.uuid4().hex[:16]}")
    revision: int = 0
    conflicts_with: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.workspace_id).strip():
            raise ValueError("workspace_id is required")
        if not str(self.subject).strip() or not str(self.predicate).strip():
            raise ValueError("subject and predicate are required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    @property
    def key(self) -> tuple[str, str]:
        return self.subject, self.predicate

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if self.expires_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        return current >= self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "status": self.status.value,
            "confidence": self.confidence,
            "source": self.source,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat() if self.expires_at else None,
            "fact_id": self.fact_id,
            "revision": self.revision,
            "conflicts_with": list(self.conflicts_with),
        }


class WorldModel:
    """Small bounded in-process world model; persistence can be layered later."""

    def __init__(self, *, max_facts_per_workspace: int = 5000) -> None:
        if max_facts_per_workspace < 1:
            raise ValueError("max_facts_per_workspace must be positive")
        self.max_facts_per_workspace = max_facts_per_workspace
        self._facts: Dict[str, Dict[tuple[str, str], WorldFact]] = {}
        self._revisions: Dict[str, int] = {}
        self._history: Dict[str, Dict[tuple[str, str], list[WorldFact]]] = {}
        self._lock = RLock()

    def observe(self, fact: WorldFact) -> WorldFact:
        """Insert or replace a fact, assigning a workspace-local revision."""
        with self._lock:
            facts = self._facts.setdefault(fact.workspace_id, {})
            revision = self._revisions.get(fact.workspace_id, 0) + 1
            history = self._history.setdefault(fact.workspace_id, {})
            prior = facts.get(fact.key)
            conflicts = ()
            status = fact.status
            if prior is not None and prior.value != fact.value:
                conflicts = tuple(dict.fromkeys((*prior.conflicts_with, prior.fact_id)))
                # Keep the newest observation as the current candidate, but make
                # the conflict explicit instead of silently overwriting truth.
                if prior.status is not BeliefStatus.CONTRADICTED:
                    prior = WorldFact(
                        workspace_id=prior.workspace_id, subject=prior.subject, predicate=prior.predicate,
                        value=prior.value, status=BeliefStatus.CONTRADICTED, confidence=prior.confidence,
                        source=prior.source, observed_at=prior.observed_at, expires_at=prior.expires_at,
                        fact_id=prior.fact_id, revision=prior.revision, conflicts_with=prior.conflicts_with + (fact.fact_id,),
                    )
                    history.setdefault(fact.key, []).append(prior)
            stored = WorldFact(
                workspace_id=fact.workspace_id,
                subject=fact.subject,
                predicate=fact.predicate,
                value=fact.value,
                status=status,
                confidence=fact.confidence,
                source=fact.source,
                observed_at=fact.observed_at,
                expires_at=fact.expires_at,
                fact_id=fact.fact_id,
                revision=revision,
                conflicts_with=conflicts,
            )
            facts[stored.key] = stored
            history.setdefault(stored.key, []).append(stored)
            # Bound history so contradiction tracking cannot become an unbounded
            # memory sink. Keep the current fact plus a small recent trail.
            if len(history[stored.key]) > 20:
                history[stored.key] = history[stored.key][-20:]
            self._revisions[fact.workspace_id] = revision
            if len(facts) > self.max_facts_per_workspace:
                oldest_key = min(facts, key=lambda key: facts[key].revision)
                del facts[oldest_key]
            return stored

    def get(self, workspace_id: str, subject: str, predicate: str) -> Optional[WorldFact]:
        with self._lock:
            fact = self._facts.get(workspace_id, {}).get((subject, predicate))
            if fact and fact.is_expired():
                return WorldFact(**{**fact.to_dict(), "status": BeliefStatus.STALE, "observed_at": fact.observed_at, "expires_at": fact.expires_at})
            return fact

    def snapshot(self, workspace_id: str) -> tuple[WorldFact, ...]:
        with self._lock:
            return tuple(self._facts.get(workspace_id, {}).values())

    def history(self, workspace_id: str, subject: str, predicate: str) -> tuple[WorldFact, ...]:
        """Return bounded historical observations for contradiction inspection."""
        with self._lock:
            return tuple(self._history.get(workspace_id, {}).get((subject, predicate), ()))

    def status(self, workspace_id: str) -> Mapping[str, Any]:
        facts = self.snapshot(workspace_id)
        stale = sum(1 for fact in facts if fact.is_expired())
        return {
            "workspace_id": workspace_id,
            "facts": len(facts),
            "stale": stale,
            "revision": self._revisions.get(workspace_id, 0),
        }
