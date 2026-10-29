"""Phase 9 — convergence/admission logic for bounded business control loops."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from models.autonomous_business_control_loop import (
    AutonomousBusinessControlLoop, ControlLoopCycle, ControlLoopObservation,
    LoopDisposition, LoopMode,
)


@dataclass(frozen=True)
class LoopEvaluation:
    disposition: LoopDisposition
    reasons: tuple[str, ...]
    proposed_action: Optional[str]
    human_takeover_required: bool


def evaluate_control_loop(
    loop: AutonomousBusinessControlLoop,
    observations: Iterable[ControlLoopObservation],
    *,
    proposed_action: Optional[str] = None,
    decision_id: Optional[str] = None,
    consecutive_failures: int = 0,
    cooldown_active: bool = False,
) -> LoopEvaluation:
    observations = tuple(observations)
    reasons: list[str] = []
    if any(o.workspace_id != loop.workspace_id for o in observations):
        return LoopEvaluation(LoopDisposition.BLOCK, ("workspace_mismatch",), None, True)
    if not loop.enabled or loop.mode == LoopMode.PAUSED:
        return LoopEvaluation(LoopDisposition.BLOCK, ("loop_disabled_or_paused",), None, True)
    if loop.mode == LoopMode.HUMAN_ONLY:
        return LoopEvaluation(LoopDisposition.ESCALATE, ("human_only_mode",), proposed_action, True)
    if not observations:
        return LoopEvaluation(LoopDisposition.OBSERVE, ("no_authoritative_observations",), None, False)
    if any(o.confidence < 0.5 for o in observations):
        reasons.append("low_confidence")
    if any(o.drift_detected for o in observations):
        reasons.append("drift_detected")
    if cooldown_active:
        return LoopEvaluation(LoopDisposition.OBSERVE, tuple(reasons + ["cooldown_active"]), None, False)
    if consecutive_failures >= loop.max_consecutive_failures:
        return LoopEvaluation(LoopDisposition.ESCALATE, tuple(reasons + ["repeated_failure"]), proposed_action, loop.human_takeover_on_failure)
    if not proposed_action:
        return LoopEvaluation(LoopDisposition.PROPOSE, tuple(reasons or ["observation_requires_decision"]), None, False)
    if proposed_action not in loop.allowed_actions:
        return LoopEvaluation(LoopDisposition.BLOCK, tuple(reasons + ["action_not_allowlisted"]), proposed_action, True)
    if loop.mode == LoopMode.SUGGEST:
        return LoopEvaluation(LoopDisposition.PROPOSE, tuple(reasons or ["suggest_mode"]), proposed_action, False)
    # Governed autonomy only means eligible to enter the existing governance path.
    return LoopEvaluation(LoopDisposition.READY_FOR_EXISTING_GOVERNANCE, tuple(reasons or ["eligible_for_existing_governance"]), proposed_action, False)


def build_cycle(loop: AutonomousBusinessControlLoop, observations: Iterable[ControlLoopObservation], **kwargs) -> ControlLoopCycle:
    observations = tuple(observations)
    evaluation = evaluate_control_loop(loop, observations, **kwargs)
    return ControlLoopCycle(
        workspace_id=loop.workspace_id,
        loop_id=loop.loop_id,
        observation_ids=tuple(o.signal_id for o in observations),
        decision_id=kwargs.get("decision_id"),
        proposed_action=evaluation.proposed_action,
        disposition=evaluation.disposition,
        reason_codes=evaluation.reasons,
        human_takeover_required=evaluation.human_takeover_required,
    )
