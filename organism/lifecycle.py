"""Integrated organism lifecycle for the Phase 37.3 cognitive loop.

This module composes the organism primitives into one deterministic, bounded
cycle.  It is an orchestration layer only: authoritative execution, security,
credentials, and durable state remain owned by their existing subsystems.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from core.organism_state import OrganismState
from organism.capabilities import CapabilityCheck, CapabilityRegistry
from organism.cognitive import CognitiveDecision, CognitiveDecisionPlane
from organism.dreaming import Dream, DreamEngine
from organism.ecosystem import EcosystemCoordinator, OrganismSignal
from organism.genome import EvolutionResult, GenomeEngine, StrategyGenome
from organism.goals import Goal
from organism.homeostasis import HomeostaticProposal, assess_homeostasis
from organism.immune import ImmuneSignal, detect_immune_signals
from organism.learning import ExecutionFeedback, LearningEngine, LearningResult
from organism.memory import MemoryStore
from organism.perception import Observation, PerceptionBuffer, ObservationKind
from organism.prediction import Prediction, PredictiveCausalEngine
from organism.state_synthesis import synthesize_state
from organism.world_model import WorldModel
from organism.hardening import IntegrityReport, validate_cycle_result


@dataclass(frozen=True)
class OrganismCycleResult:
    """Complete derived result of one organism reasoning cycle."""

    workspace_id: str
    state: OrganismState
    observations: tuple[Observation, ...] = ()
    learning: tuple[LearningResult, ...] = ()
    predictions: tuple[Prediction, ...] = ()
    dreams: tuple[Dream, ...] = ()
    evolution: Optional[EvolutionResult] = None
    capability_checks: tuple[CapabilityCheck, ...] = ()
    homeostasis: tuple[HomeostaticProposal, ...] = ()
    immune: tuple[ImmuneSignal, ...] = ()
    decision: Optional[CognitiveDecision] = None
    ecosystem_signals: tuple[OrganismSignal, ...] = ()
    cycle_id: str = ""
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    integrity: IntegrityReport | None = None


class OrganismRuntime:
    """Single composition boundary for one workspace's organism.

    The runtime deliberately accepts authoritative inputs instead of reaching
    into providers itself. This makes the loop testable and keeps perception,
    learning, planning and governance downstream of real subsystem truth.
    """

    MAX_GOALS = 64
    MAX_ACTIONS_PER_CYCLE = 20
    MAX_LEARNING_ITEMS = 32

    def __init__(
        self,
        workspace_id: str,
        *,
        capability_registry: CapabilityRegistry | None = None,
        ecosystem: EcosystemCoordinator | None = None,
        cognitive: CognitiveDecisionPlane | None = None,
    ) -> None:
        if not str(workspace_id).strip():
            raise ValueError("workspace_id is required")
        self.workspace_id = workspace_id
        self.perception = PerceptionBuffer()
        self.memory = MemoryStore()
        self.world_model = WorldModel()
        self.learning = LearningEngine(self.memory, self.world_model)
        self.predictor = PredictiveCausalEngine(self.memory, self.world_model)
        self.dreamer = DreamEngine(self.memory, self.world_model, self.predictor)
        self.genome = GenomeEngine(self.dreamer)
        self.capabilities = capability_registry or CapabilityRegistry()
        self.ecosystem = ecosystem
        self.cognitive = cognitive or CognitiveDecisionPlane()
        self.state: OrganismState | None = None
        self.persistence_store = None
        self.recovery_status = "not_attempted"
        self._cycle_number = 0

    def ingest(self, observation: Observation) -> Observation:
        """Ingest one observation, enforcing the runtime tenant boundary."""
        if observation.workspace_id != self.workspace_id:
            raise ValueError("observation workspace_id does not match organism workspace")
        return self.perception.ingest(observation)

    def receive_ecosystem_signals(
        self,
        ecosystem_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[OrganismSignal, ...]:
        if self.ecosystem is None:
            return ()
        signals = self.ecosystem.signals_for(ecosystem_id, self.workspace_id, now=now)
        for signal in signals:
            # External ecosystem signals are observations, never authority. Cap
            # their trust and preserve the source workspace in provenance.
            self.ingest(Observation(
                workspace_id=self.workspace_id,
                kind=ObservationKind.EXTERNAL,
                source=f"ecosystem:{signal.ecosystem_id}",
                value={"subject": signal.subject, "value": signal.value, "kind": signal.kind.value},
                confidence=min(0.5, float(signal.confidence)),
                observed_at=signal.created_at,
                metadata={"source_workspace_id": signal.source_workspace_id},
            ))
        return signals

    def cycle(
        self,
        *,
        goals: Sequence[Goal] = (),
        execution_feedback: Iterable[ExecutionFeedback] = (),
        actions: Sequence[str] = (),
        strategy: StrategyGenome | None = None,
        strategy_candidates: Iterable[StrategyGenome] = (),
        strategy_action_pool: Sequence[str] = (),
        available_capabilities: Iterable[str] = (),
        proposed_action: str | None = None,
        proposed_action_description: str = "",
        proposed_action_params: Mapping[str, Any] | None = None,
        topology_healthy: bool = True,
        source_versions: Mapping[str, str] | None = None,
    ) -> OrganismCycleResult:
        """Run one bounded perceive→learn→synthesize→respond→imagine→decide cycle."""
        self._cycle_number += 1
        cycle_id = f"cycle_{self._cycle_number}"
        scoped_goals = tuple(goals)
        if len(scoped_goals) > self.MAX_GOALS:
            raise ValueError(f"goals must contain at most {self.MAX_GOALS} items")
        if any(goal.workspace_id != self.workspace_id for goal in scoped_goals):
            raise ValueError("all goals must belong to organism workspace")

        feedback_items = tuple(execution_feedback)
        if len(feedback_items) > self.MAX_LEARNING_ITEMS:
            raise ValueError(f"execution_feedback must contain at most {self.MAX_LEARNING_ITEMS} items")
        if any(item.workspace_id != self.workspace_id for item in feedback_items):
            raise ValueError("all execution feedback must belong to organism workspace")

        # 1. Durable execution truth becomes experience and world evidence.
        goal_by_id = {goal.goal_id: goal for goal in scoped_goals}
        learning_results: list[LearningResult] = []
        for feedback in feedback_items:
            goal = goal_by_id.get(feedback.goal_id) if feedback.goal_id else None
            if feedback.goal_id and goal is None:
                raise ValueError("execution feedback goal_id does not belong to organism workspace goals")
            learning_results.append(self.learning.learn(feedback, goal=goal))

        # 2. Learning itself is a perception source for the next state snapshot.
        for result in learning_results:
            self.ingest(Observation(
                workspace_id=self.workspace_id,
                kind=ObservationKind.LEARNING,
                source="organism_learning",
                value={"action": result.experience.action, "outcome": result.experience.outcome.value},
                confidence=result.experience.confidence,
            ))

        observations = self.perception.recent(self.workspace_id)
        active_goal = next((g for g in scoped_goals if g.state.value in {"active", "at_risk", "proposed"}), None)
        state = synthesize_state(
            self.workspace_id,
            observations=observations,
            goals=scoped_goals,
            topology_healthy=topology_healthy,
            source_versions=source_versions,
        )
        self.state = state

        # 3. Homeostasis and immunity inspect the same canonical state. They
        # produce proposals/signals only; no remediation occurs here.
        homeostasis = assess_homeostasis(state)
        immune = detect_immune_signals(state)

        # 4. Predictions and dreams are explicitly bounded and read-only.
        action_list = tuple(dict.fromkeys(str(a).strip() for a in actions if str(a).strip()))[: self.MAX_ACTIONS_PER_CYCLE]
        predictions = tuple(self.predictor.predict_action(self.workspace_id, action) for action in action_list)
        dreams: list[Dream] = []
        if action_list:
            dreams.append(self.dreamer.simulate(self.workspace_id, action_list, goal=active_goal))

        evolution = None
        if strategy is not None:
            if strategy.workspace_id != self.workspace_id:
                raise ValueError("strategy workspace_id does not match organism workspace")
            evolution = self.genome.evolve(
                self.workspace_id,
                strategy,
                candidates=tuple(strategy_candidates),
                goal=active_goal,
                action_pool=tuple(strategy_action_pool),
            )

        # 5. Capabilities gate cognitive consideration, but never substitute for
        # DecisionGate/Guardian authority. Missing capability metadata is simply
        # a bounded negative signal.
        checks: list[CapabilityCheck] = []
        if proposed_action:
            checks.append(self.capabilities.check(
                self.workspace_id,
                proposed_action,
                authority=active_goal.authority if active_goal else "suggest",
                available=available_capabilities,
            ))

        decision = None
        if proposed_action and active_goal is not None:
            check = checks[0] if checks else None
            if check is not None and not check.allowed:
                decision = self.cognitive._blocked(state, proposed_action, f"capability boundary: {check.reason}")
            else:
                decision = _run_cognitive(self.cognitive, state, active_goal, proposed_action, proposed_action_description, proposed_action_params)

        completed = datetime.now(timezone.utc)
        result = OrganismCycleResult(
            workspace_id=self.workspace_id,
            state=state,
            observations=observations,
            learning=tuple(learning_results),
            predictions=predictions,
            dreams=tuple(dreams),
            evolution=evolution,
            capability_checks=tuple(checks),
            homeostasis=homeostasis,
            immune=immune,
            decision=decision,
            cycle_id=cycle_id,
            completed_at=completed,
        )
        integrity = validate_cycle_result(result)
        if not integrity.valid:
            raise RuntimeError("organism cycle integrity violation: " + "; ".join(integrity.violations))
        return OrganismCycleResult(**{**result.__dict__, "integrity": integrity})


def _run_cognitive(
    cognitive: CognitiveDecisionPlane,
    state: OrganismState,
    goal: Goal,
    action: str,
    description: str,
    params: Mapping[str, Any] | None,
) -> CognitiveDecision:
    """Run the async cognitive plane from sync orchestration without executing."""
    # The runtime API is intentionally synchronous for deterministic callers.
    # It is not called from an event-loop-owned path; async applications should
    # use the helper below instead.
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(cognitive.evaluate(
            state=state, goal=goal, action_name=action,
            description=description, action_params=params,
        ))
    raise RuntimeError("use cycle_async() when invoking OrganismRuntime from an active event loop")


async def cycle_async(runtime: OrganismRuntime, **kwargs: Any) -> OrganismCycleResult:
    """Async-safe variant for FastAPI/event-loop callers.

    The core cycle is synchronous except for cognitive governance. To preserve
    exactly one composition model, this helper performs the non-cognitive work
    through a small internal path and evaluates the decision asynchronously.
    """
    proposed_action = kwargs.pop("proposed_action", None)
    # Avoid nesting event loops by running the cycle without decision first.
    result = runtime.cycle(proposed_action=None, **kwargs)
    if proposed_action:
        goals = tuple(kwargs.get("goals", ()))
        active_goal = next((g for g in goals if g.workspace_id == runtime.workspace_id and g.state.value in {"active", "at_risk", "proposed"}), None)
        if active_goal is not None:
            check = runtime.capabilities.check(
                runtime.workspace_id,
                proposed_action,
                authority=active_goal.authority,
                available=kwargs.get("available_capabilities", ()),
            )
            if check.allowed:
                decision = await runtime.cognitive.evaluate(
                    state=result.state,
                    goal=active_goal,
                    action_name=proposed_action,
                    description=kwargs.get("proposed_action_description", ""),
                    action_params=kwargs.get("proposed_action_params"),
                )
            else:
                decision = runtime.cognitive._blocked(result.state, proposed_action, f"capability boundary: {check.reason}")
            return OrganismCycleResult(**{**result.__dict__, "decision": decision, "capability_checks": (check,)})
    return result


__all__ = ["OrganismCycleResult", "OrganismRuntime", "cycle_async"]
