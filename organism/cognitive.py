"""Canonical cognitive decision plane for the organism.

This is an orchestration boundary, not a second Council.  It combines the
canonical organism state/goal contracts with the existing SafetyPolicy,
ActionPolicy, CouncilOrchestrator and DecisionGate.  It never executes an
external action and never grants authority beyond the goal/state policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
import uuid

from core.decision_gate import DecisionGate, DecisionSource
from core.organism_state import OrganismState, OperatingMode
from council.models import DecisionState, Proposal, RiskLevel
from council.orchestration.council_orchestrator import CouncilOrchestrator
from council.policies.action_policy import ActionPolicy, ActionClass
from council.policies.safety_policy import SafetyPolicy
from organism.goals import Goal


@dataclass(frozen=True)
class CognitiveDecision:
    """Decision-plane result; execution remains outside this object."""

    workspace_id: str
    action_name: str
    decision: DecisionState
    decision_id: Optional[str]
    correlation_id: str
    risk_level: RiskLevel
    confidence: float
    reason: str
    requires_human_approval: bool = False
    blocked: bool = False
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def executable(self) -> bool:
        return self.decision in {
            DecisionState.APPROVED,
            DecisionState.APPROVED_WITH_WARNINGS,
        } and not self.requires_human_approval and not self.blocked and bool(self.decision_id)


class CognitiveDecisionPlane:
    """Single entry point for turning organism intent into an authorization."""

    def __init__(
        self,
        *,
        council: Optional[CouncilOrchestrator] = None,
        decision_gate: Optional[DecisionGate] = None,
        action_policy: Optional[ActionPolicy] = None,
        safety_policy: Optional[SafetyPolicy] = None,
    ) -> None:
        self.council = council or CouncilOrchestrator()
        self.decision_gate = decision_gate or DecisionGate()
        self.action_policy = action_policy or ActionPolicy()
        self.safety_policy = safety_policy or SafetyPolicy()

    async def evaluate(
        self,
        *,
        state: OrganismState,
        goal: Goal,
        action_name: str,
        description: str = "",
        action_params: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> CognitiveDecision:
        if goal.workspace_id != state.workspace_id:
            return self._blocked(state, action_name, "goal workspace does not match organism workspace")

        correlation_id = uuid.uuid4().hex
        action_class = self.action_policy.classify(action_name)
        requirements = self.action_policy.get_requirements(action_class)
        risk = self._risk(action_class)
        warnings: list[str] = []

        # Safety is a hard boundary and runs before any deliberation.
        if self.safety_policy.is_forbidden(action_name, description):
            return self._blocked(
                state, action_name, "action is forbidden by safety policy",
                correlation_id=correlation_id, risk_level=risk,
            )

        if state.mode in {OperatingMode.QUARANTINED, OperatingMode.STOPPED}:
            return self._blocked(
                state, action_name, f"organism mode '{state.mode.value}' cannot authorize actions",
                correlation_id=correlation_id, risk_level=risk,
            )

        # A goal can only authorize up to its declared authority.  In particular,
        # a suggest-only goal cannot silently become an execution authority.
        required_authority = "publish" if action_name.lower().find("publish") >= 0 else "execute"
        if not self._authority_allows(goal.authority, required_authority):
            return self._blocked(
                state, action_name,
                f"goal authority '{goal.authority}' cannot authorize '{required_authority}'",
                correlation_id=correlation_id, risk_level=risk,
            )

        if state.security_posture < 0.35 or not state.topology_healthy:
            return self._blocked(
                state, action_name, "unsafe organism state: security/topology below execution threshold",
                correlation_id=correlation_id, risk_level=risk,
            )

        human_required = bool(requirements.get("human_approval")) or self.safety_policy.requires_human_approval(action_name)
        if human_required:
            warnings.append("human approval required; no autonomous authorization issued")

        proposal = Proposal(
            action_name=action_name,
            source_engine="organism.cognitive",
            description=description,
            proposal_type="organism_goal",
            action_params=dict(action_params or {}),
            metadata={
                **dict(metadata or {}),
                "workspace_id": state.workspace_id,
                "goal_id": goal.goal_id,
                "organism_mode": state.mode.value,
                "organism_confidence": state.confidence.value,
                "organism_risk": state.risk,
                "correlation_id": correlation_id,
            },
            risk_level=risk,
        )

        result = await self.council.deliberate(proposal)
        approved = result.decision in {DecisionState.APPROVED, DecisionState.APPROVED_WITH_WARNINGS}

        # Critical/human-gated outcomes are represented as escalation, never as
        # a decision that can accidentally flow into an executor.
        if human_required and approved:
            return CognitiveDecision(
                workspace_id=state.workspace_id,
                action_name=action_name,
                decision=DecisionState.ESCALATED,
                decision_id=None,
                correlation_id=correlation_id,
                risk_level=result.risk_level,
                confidence=result.confidence,
                reason="Council approved, but policy requires human approval",
                requires_human_approval=True,
                warnings=tuple(warnings + list(result.warnings)),
                metadata={"goal_id": goal.goal_id, "tier_used": result.tier_used},
            )

        if not approved:
            return CognitiveDecision(
                workspace_id=state.workspace_id,
                action_name=action_name,
                decision=result.decision,
                decision_id=None,
                correlation_id=correlation_id,
                risk_level=result.risk_level,
                confidence=result.confidence,
                reason="Council did not authorize the proposed action",
                warnings=tuple(warnings + list(result.warnings)),
                metadata={"goal_id": goal.goal_id, "tier_used": result.tier_used},
            )

        gate_decision = self.decision_gate.issue(
            source=DecisionSource.COUNCIL,
            workspace_id=state.workspace_id,
            action_name=action_name,
            approved=True,
            correlation_id=correlation_id,
            risk_level=result.risk_level.value,
            reason="Council authorized organism goal action",
            votes=[v.__dict__ for v in result.votes],
            metadata={
                "goal_id": goal.goal_id,
                "tier_used": result.tier_used,
                "confidence": result.confidence,
            },
        )
        return CognitiveDecision(
            workspace_id=state.workspace_id,
            action_name=action_name,
            decision=result.decision,
            decision_id=gate_decision.decision_id,
            correlation_id=correlation_id,
            risk_level=result.risk_level,
            confidence=result.confidence,
            reason="Council authorized organism goal action",
            warnings=tuple(warnings + list(result.warnings)),
            metadata={"goal_id": goal.goal_id, "tier_used": result.tier_used},
        )

    @staticmethod
    def _authority_allows(actual: str, required: str) -> bool:
        levels = {"suggest": 0, "execute": 1, "publish": 2}
        return levels.get(actual, -1) >= levels[required]

    @staticmethod
    def _risk(action_class: ActionClass) -> RiskLevel:
        return {
            ActionClass.READ_ONLY: RiskLevel.TRIVIAL,
            ActionClass.LOW_RISK: RiskLevel.LOW,
            ActionClass.MEDIUM_RISK: RiskLevel.MEDIUM,
            ActionClass.HIGH_RISK: RiskLevel.HIGH,
            ActionClass.CRITICAL: RiskLevel.CRITICAL,
            ActionClass.IRREVERSIBLE: RiskLevel.CRITICAL,
        }.get(action_class, RiskLevel.MEDIUM)

    @staticmethod
    def _blocked(
        state: OrganismState,
        action_name: str,
        reason: str,
        *,
        correlation_id: Optional[str] = None,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
    ) -> CognitiveDecision:
        return CognitiveDecision(
            workspace_id=state.workspace_id,
            action_name=action_name,
            decision=DecisionState.BLOCKED,
            decision_id=None,
            correlation_id=correlation_id or uuid.uuid4().hex,
            risk_level=risk_level,
            confidence=1.0,
            reason=reason,
            blocked=True,
        )
