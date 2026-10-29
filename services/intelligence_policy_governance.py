"""Phase 5 Intelligence Policy and Decision Governance.

Converts an intelligence recommendation into a governed ActionIntent only when
explicit policy conditions are satisfied. This module is intentionally unable
to authorize external execution or invoke the Action Kernel.

Canonical boundary:
    Decision -> Recommendation -> Policy Evaluation -> Governed ActionIntent
                                      |
                                      +-> APPROVED / REQUIRES_APPROVAL / HOLD / REJECTED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from services.intelligence_evidence_graph import EvidenceNode, IntelligenceEvidenceGraph


class RiskClass(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class GovernanceDisposition(str, Enum):
    APPROVED = "APPROVED"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    HOLD_UNCERTAIN = "HOLD_UNCERTAIN"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class IntelligencePolicy:
    """Deterministic policy for the intelligence-to-action boundary."""

    policy_id: str
    workspace_id: str
    min_confidence_by_risk: Mapping[RiskClass, float] = field(
        default_factory=lambda: {
            RiskClass.LOW: 0.60,
            RiskClass.MEDIUM: 0.75,
            RiskClass.HIGH: 0.90,
            RiskClass.CRITICAL: 0.95,
        }
    )
    approval_required_by_risk: Mapping[RiskClass, bool] = field(
        default_factory=lambda: {
            RiskClass.LOW: False,
            RiskClass.MEDIUM: False,
            RiskClass.HIGH: True,
            RiskClass.CRITICAL: True,
        }
    )
    allowed_action_types: frozenset[str] | None = None
    blocked_action_types: frozenset[str] = frozenset()
    max_uncertainty: float = 0.0
    minimum_evidence_refs: int = 1
    allowed_autonomy_levels: frozenset[str] = frozenset({"L0", "L1", "L2", "L3", "L4", "L5"})

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if not self.workspace_id.strip():
            raise ValueError("workspace_id is required")
        for risk, threshold in self.min_confidence_by_risk.items():
            if not 0.0 <= float(threshold) <= 1.0:
                raise ValueError(f"invalid confidence threshold for {risk}")
        if not 0.0 <= self.max_uncertainty <= 1.0:
            raise ValueError("max_uncertainty must be between 0 and 1")
        if self.minimum_evidence_refs < 1:
            raise ValueError("minimum_evidence_refs must be >= 1")


@dataclass(frozen=True)
class GovernanceEvaluation:
    policy_id: str
    workspace_id: str
    recommendation_node_id: str
    action_type: str
    risk_class: RiskClass
    confidence: float
    uncertainty: float
    evidence_ref_count: int
    autonomy_level: str
    disposition: GovernanceDisposition
    reasons: tuple[str, ...]
    requires_human_approval: bool
    governed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "workspace_id": self.workspace_id,
            "recommendation_node_id": self.recommendation_node_id,
            "action_type": self.action_type,
            "risk_class": self.risk_class.value,
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "evidence_ref_count": self.evidence_ref_count,
            "autonomy_level": self.autonomy_level,
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "requires_human_approval": self.requires_human_approval,
            "governed": self.governed,
        }


class IntelligencePolicyGovernor:
    """Pure policy gate between intelligence recommendations and ActionIntent."""

    def evaluate(
        self,
        *,
        recommendation: EvidenceNode,
        policy: IntelligencePolicy,
    ) -> GovernanceEvaluation:
        if recommendation.node_type != "recommendation":
            raise ValueError("governance requires a recommendation node")
        if recommendation.workspace_id != policy.workspace_id:
            raise ValueError("recommendation workspace does not match policy workspace")

        payload = recommendation.payload
        action_type = str(payload.get("action_type") or "")
        if not action_type:
            raise ValueError("recommendation.action_type is required")

        risk = self._risk(payload.get("risk_class", RiskClass.MEDIUM))
        confidence = self._bounded_float(payload.get("confidence", 0.0), "confidence")
        uncertainty = self._bounded_float(payload.get("uncertainty", 1.0), "uncertainty")
        autonomy = str(payload.get("autonomy_level", "L1"))
        evidence_refs = payload.get("evidence_refs", ())
        evidence_count = len(evidence_refs) if isinstance(evidence_refs, (list, tuple, set, frozenset)) else 0

        reasons: list[str] = []
        blocked = False

        if policy.allowed_action_types is not None and action_type not in policy.allowed_action_types:
            reasons.append("action_type_not_allowed")
            blocked = True
        if action_type in policy.blocked_action_types:
            reasons.append("action_type_blocked")
            blocked = True
        if autonomy not in policy.allowed_autonomy_levels:
            reasons.append("autonomy_level_not_allowed")
            blocked = True
        if evidence_count < policy.minimum_evidence_refs:
            reasons.append("insufficient_evidence")
            blocked = True

        threshold = float(policy.min_confidence_by_risk[risk])
        if confidence < threshold:
            reasons.append("confidence_below_threshold")
            blocked = True

        if uncertainty > policy.max_uncertainty:
            reasons.append("uncertainty_above_policy_limit")
            return GovernanceEvaluation(
                policy.policy_id, policy.workspace_id, recommendation.node_id, action_type,
                risk, confidence, uncertainty, evidence_count, autonomy,
                GovernanceDisposition.HOLD_UNCERTAIN, tuple(reasons),
                requires_human_approval=False, governed=False,
            )

        if blocked:
            return GovernanceEvaluation(
                policy.policy_id, policy.workspace_id, recommendation.node_id, action_type,
                risk, confidence, uncertainty, evidence_count, autonomy,
                GovernanceDisposition.REJECTED, tuple(reasons),
                requires_human_approval=False, governed=False,
            )

        approval = bool(policy.approval_required_by_risk[risk])
        disposition = GovernanceDisposition.REQUIRES_APPROVAL if approval else GovernanceDisposition.APPROVED
        if approval:
            reasons.append("human_approval_required_by_risk_policy")
        else:
            reasons.append("policy_conditions_satisfied")

        return GovernanceEvaluation(
            policy.policy_id, policy.workspace_id, recommendation.node_id, action_type,
            risk, confidence, uncertainty, evidence_count, autonomy,
            disposition, tuple(reasons), requires_human_approval=approval, governed=True,
        )

    def govern_to_action_intent(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        recommendation_node_id: str,
        policy: IntelligencePolicy,
        idempotency_key: str,
        extra_action_fields: Mapping[str, Any] | None = None,
    ) -> tuple[GovernanceEvaluation, EvidenceNode | None]:
        recommendation = self._recommendation(graph, recommendation_node_id)
        evaluation = self.evaluate(recommendation=recommendation, policy=policy)

        if evaluation.disposition is not GovernanceDisposition.APPROVED:
            return evaluation, None

        action = {
            "action_type": evaluation.action_type,
            "idempotency_key": idempotency_key,
            "risk_class": evaluation.risk_class.value,
            "confidence": evaluation.confidence,
            "uncertainty": evaluation.uncertainty,
            "governance": {
                "policy_id": policy.policy_id,
                "disposition": evaluation.disposition.value,
                "requires_human_approval": evaluation.requires_human_approval,
            },
        }
        action.update(dict(extra_action_fields or {}))
        node = graph.add_action_intent(
            workspace_id=policy.workspace_id,
            action_intent=action,
            recommendation_node_id=recommendation_node_id,
            provenance={
                "governance_policy_id": policy.policy_id,
                "governance_disposition": evaluation.disposition.value,
            },
        )
        # The governance service owns the intelligence-to-action handoff.
        # Admission is intentionally performed after persistence in the graph
        # and before the ActionIntent is returned to an execution-facing caller.
        from services.intelligence_action_intent_admission import IntelligenceActionIntentAdmissionGate
        IntelligenceActionIntentAdmissionGate().admit(
            graph=graph,
            action_intent_node_id=node.node_id,
            policy=policy,
            evaluation=evaluation,
        )
        return evaluation, node

    @staticmethod
    def _recommendation(graph: IntelligenceEvidenceGraph, node_id: str) -> EvidenceNode:
        try:
            # EvidenceNode is intentionally immutable; the graph's public snapshot is
            # not a suitable mutation path, so validate through a lineage projection.
            snapshot = graph.snapshot()
            row = next(n for n in snapshot["nodes"] if n["node_id"] == node_id)
        except StopIteration as exc:
            raise ValueError(f"unknown recommendation node: {node_id}") from exc
        if row["node_type"] != "recommendation":
            raise ValueError("governance requires a recommendation node")
        return EvidenceNode(
            node_id=row["node_id"], node_type=row["node_type"], workspace_id=row["workspace_id"],
            payload=row["payload"], provenance=row["provenance"], created_at=row["created_at"],
            schema_version=row["schema_version"], fingerprint=row.get("fingerprint", ""),
        )

    @staticmethod
    def _risk(value: Any) -> RiskClass:
        try:
            return value if isinstance(value, RiskClass) else RiskClass(str(value).upper())
        except ValueError as exc:
            raise ValueError(f"unsupported risk class: {value}") from exc

    @staticmethod
    def _bounded_float(value: Any, name: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not 0.0 <= result <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
        return result
