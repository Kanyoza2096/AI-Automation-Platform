"""Bounded digital genome for strategy evolution.

The genome contains declarative strategy/configuration only. It never stores executable
code, credentials, permissions, or authority. Variants are evaluated in the offline
DreamEngine before an operator/cognitive governance layer can consider them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any, Iterable, Mapping, Sequence

from organism.dreaming import Dream, DreamEngine
from organism.goals import Goal


@dataclass(frozen=True)
class StrategyGenome:
    workspace_id: str
    name: str
    actions: tuple[str, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)
    lineage_id: str = field(default_factory=lambda: f"lin_{uuid.uuid4().hex[:16]}")
    parent_id: str | None = None
    generation: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    genome_id: str = field(default_factory=lambda: f"gen_{uuid.uuid4().hex[:16]}")

    def __post_init__(self) -> None:
        if not self.workspace_id.strip() or not self.name.strip():
            raise ValueError("workspace_id and name are required")
        if not self.actions or len(self.actions) > 20:
            raise ValueError("actions must contain 1-20 items")
        if any(not str(a).strip() for a in self.actions):
            raise ValueError("actions must be non-empty")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        # Declarative-only boundary: values are JSON-like data, never callables/code.
        for key, value in self.parameters.items():
            if not str(key).strip() or callable(value):
                raise ValueError("parameters must be declarative and non-callable")
            try:
                json.dumps(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("parameters must be JSON-serializable") from exc

    def fingerprint(self) -> str:
        payload = json.dumps({"name": self.name, "actions": self.actions, "parameters": self.parameters}, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def variant(self, *, actions: Sequence[str] | None = None, parameters: Mapping[str, Any] | None = None, name: str | None = None) -> "StrategyGenome":
        return StrategyGenome(
            workspace_id=self.workspace_id,
            name=name or f"{self.name}-g{self.generation + 1}",
            actions=tuple(actions or self.actions),
            parameters=dict(parameters if parameters is not None else self.parameters),
            lineage_id=self.lineage_id,
            parent_id=self.genome_id,
            generation=self.generation + 1,
        )


@dataclass(frozen=True)
class GenomeFitness:
    genome_id: str
    workspace_id: str
    score: float
    risk: float
    confidence: float
    dream: Dream
    evaluated_at: datetime


@dataclass(frozen=True)
class EvolutionResult:
    workspace_id: str
    candidates: tuple[StrategyGenome, ...]
    fitness: tuple[GenomeFitness, ...]
    best: StrategyGenome | None


class GenomeEngine:
    """Create and rank bounded strategy variants using offline simulation only."""

    MAX_CANDIDATES = 32
    MAX_MUTATIONS = 8

    def __init__(self, dream_engine: DreamEngine) -> None:
        self.dream_engine = dream_engine

    def mutate(self, genome: StrategyGenome, *, action_pool: Sequence[str] = ()) -> tuple[StrategyGenome, ...]:
        pool = tuple(dict.fromkeys(str(a).strip() for a in action_pool if str(a).strip()))
        variants: list[StrategyGenome] = []
        # Bounded declarative mutations: add/remove/replace an action and adjust
        # numeric configuration. No generated executable material is permitted.
        if len(genome.actions) < 20 and pool:
            for action in pool:
                if action not in genome.actions:
                    variants.append(genome.variant(actions=genome.actions + (action,)))
                    if len(variants) >= self.MAX_MUTATIONS:
                        break
        if len(genome.actions) > 1 and len(variants) < self.MAX_MUTATIONS:
            variants.append(genome.variant(actions=genome.actions[:-1]))
        if pool and len(variants) < self.MAX_MUTATIONS:
            replacement = next((a for a in pool if a != genome.actions[0]), None)
            if replacement:
                variants.append(genome.variant(actions=(replacement,) + genome.actions[1:]))
        return tuple(variants[: self.MAX_MUTATIONS])

    def evolve(
        self,
        workspace_id: str,
        seed: StrategyGenome,
        *,
        candidates: Iterable[StrategyGenome] = (),
        goal: Goal | None = None,
        action_pool: Sequence[str] = (),
    ) -> EvolutionResult:
        if seed.workspace_id != workspace_id:
            raise ValueError("seed workspace_id does not match workspace_id")
        supplied = tuple(candidates)
        if any(candidate.workspace_id != workspace_id for candidate in supplied):
            raise ValueError("all candidate genomes must belong to workspace_id")
        generated = self.mutate(seed, action_pool=action_pool) if action_pool else ()
        pool_items = (seed,) + supplied + generated
        seen_ids: set[str] = set()
        pool: tuple[StrategyGenome, ...] = tuple(
            genome for genome in pool_items
            if not (genome.genome_id in seen_ids or seen_ids.add(genome.genome_id))
        )
        if len(pool) > self.MAX_CANDIDATES:
            raise ValueError(f"candidates must contain at most {self.MAX_CANDIDATES} items")
        scored: list[GenomeFitness] = []
        for genome in pool:
            dream = self.dream_engine.simulate(workspace_id, genome.actions, goal=goal)
            scored.append(GenomeFitness(genome.genome_id, workspace_id, dream.score, dream.risk, dream.confidence, dream, datetime.now(timezone.utc)))
        scored.sort(key=lambda f: (f.score, f.confidence, -f.risk), reverse=True)
        best = next((g for f in scored for g in pool if g.genome_id == f.genome_id), None)
        return EvolutionResult(workspace_id, pool, tuple(scored), best)
