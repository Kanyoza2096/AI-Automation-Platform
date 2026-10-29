"""Phase 5.5 — canonical intelligence decision construction.

Learning context is consumed as bounded advisory evidence.  By default it is
reference-only: it is attached to the decision provenance and cannot alter the
semantic decision fields that governance evaluates.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from services.intelligence_evidence_graph import EvidenceNode, IntelligenceEvidenceGraph
from services.intelligence_learning_decision_context import (
    LearningDecisionContext,
    validate_learning_decision_context,
)


class LearningUseMode(str, Enum):
    IGNORE = "IGNORE"
    REFERENCE_ONLY = "REFERENCE_ONLY"


@dataclass(frozen=True)
class IntelligenceDecisionPolicy:
    policy_id: str
    workspace_id: str
    learning_use: LearningUseMode = LearningUseMode.REFERENCE_ONLY

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("decision policy_id is required")
        if not self.workspace_id.strip():
            raise ValueError("decision workspace_id is required")


@dataclass(frozen=True)
class IntelligenceDecisionResult:
    decision: EvidenceNode
    recommendation: EvidenceNode


def decision_semantic_fingerprint(
    *,
    policy_id: str,
    workspace_id: str,
    learning_use_mode: str,
    learning_context_id: str | None,
    learning_source_fingerprints: list[str] | tuple[str, ...],
    ai_provenance: Mapping[str, Any],
    interpretation: Mapping[str, Any],
    decision: Mapping[str, Any],
    recommendation: Mapping[str, Any],
) -> str:
    """Deterministic semantic identity for a constructed intelligence decision.

    Ephemeral evidence node IDs/timestamps are intentionally excluded.
    """
    material = {
        "policy_id": policy_id,
        "workspace_id": workspace_id,
        "learning_use_mode": learning_use_mode,
        "learning_context_id": learning_context_id,
        "learning_source_fingerprints": list(learning_source_fingerprints),
        "ai_receipt_fingerprint": ai_provenance.get("ai_receipt_fingerprint"),
        "ai_interpretation_semantic_fingerprint": ai_provenance.get("ai_interpretation_semantic_fingerprint"),
        "ai_provider": ai_provenance.get("ai_provider"),
        "ai_model": ai_provenance.get("ai_model"),
        "ai_correlation_id": ai_provenance.get("ai_correlation_id"),
        "ai_request_fingerprint": ai_provenance.get("ai_request_fingerprint"),
        "ai_status": ai_provenance.get("ai_status"),
        "interpretation_semantic": {
            "workspace_id": interpretation.get("workspace_id"),
            "payload": dict(interpretation.get("payload", {})),
            "provenance": {k: v for k, v in dict(interpretation.get("provenance", {})).items() if k != "signal_node_id"},
        },
        "decision": dict(decision),
        "recommendation": dict(recommendation),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class IntelligenceDecisionEngine:
    """Construct decisions/recommendations without governance or execution authority."""

    def construct(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        interpretation_node_id: str,
        policy: IntelligenceDecisionPolicy,
        decision: Mapping[str, Any],
        recommendation: Mapping[str, Any],
        learning_context: LearningDecisionContext | Mapping[str, Any] | None = None,
    ) -> IntelligenceDecisionResult:
        interpretation = self._require(graph, interpretation_node_id)
        if interpretation.node_type != "interpretation":
            raise ValueError("decision construction requires an interpretation node")
        if interpretation.workspace_id != policy.workspace_id:
            raise PermissionError("interpretation workspace does not match decision policy")

        context_value: Mapping[str, Any] | None = None
        if learning_context is not None:
            context_value = (
                learning_context.to_dict()
                if isinstance(learning_context, LearningDecisionContext)
                else dict(learning_context)
            )
            validate_learning_decision_context(context_value)
            if str(context_value["workspace_id"]) != policy.workspace_id:
                raise PermissionError("learning context workspace does not match decision policy")

        decision_payload = dict(decision)
        recommendation_payload = dict(recommendation)
        required_recommendation = ("action_type", "risk_class", "confidence", "uncertainty", "evidence_refs", "autonomy_level")
        missing = [key for key in required_recommendation if key not in recommendation_payload]
        if missing:
            raise ValueError(f"recommendation missing required fields: {missing}")

        # Learning is deliberately non-semantic in this phase.  It is attached
        # as auditable provenance, while governance-relevant recommendation
        # fields remain exactly caller supplied.
        ai_provenance = {
            key: interpretation.provenance[key]
            for key in ("ai_receipt_fingerprint", "ai_interpretation_semantic_fingerprint", "ai_provider", "ai_model", "ai_correlation_id", "ai_request_fingerprint", "ai_status")
            if key in interpretation.provenance
        }
        if "ai_receipt_fingerprint" in interpretation.provenance and interpretation.provenance.get("ai_authority") != "none":
            raise ValueError("AI interpretation provenance must declare ai_authority=none")

        learning_provenance = {
            "learning_use_policy_id": policy.policy_id,
            "learning_use_mode": policy.learning_use.value,
            "learning_context_id": None,
            "learning_source_fingerprints": [],
            "learning_advisory_only": True,
        }
        if context_value is not None and policy.learning_use is not LearningUseMode.IGNORE:
            learning_provenance.update(
                {
                    "learning_context_id": context_value["context_id"],
                    "learning_source_fingerprints": list(context_value["source_fingerprints"]),
                }
            )

        decision_fingerprint = decision_semantic_fingerprint(
            policy_id=policy.policy_id,
            workspace_id=policy.workspace_id,
            learning_use_mode=policy.learning_use.value,
            learning_context_id=learning_provenance["learning_context_id"],
            learning_source_fingerprints=learning_provenance["learning_source_fingerprints"],
            ai_provenance=ai_provenance,
            interpretation={
                "workspace_id": interpretation.workspace_id,
                "payload": dict(interpretation.payload),
                "provenance": dict(interpretation.provenance),
            },
            decision=decision_payload,
            recommendation=recommendation_payload,
        )
        learning_provenance["decision_fingerprint"] = decision_fingerprint

        decision_node = graph.add_node(
            node_type="decision",
            workspace_id=policy.workspace_id,
            payload=decision_payload,
            provenance={
                "interpretation_node_id": interpretation_node_id,
                **ai_provenance,
                **learning_provenance,
            },
        )
        graph.link(interpretation_node_id, decision_node.node_id, relation="supports")

        recommendation_node = graph.add_node(
            node_type="recommendation",
            workspace_id=policy.workspace_id,
            payload=recommendation_payload,
            provenance={
                "decision_node_id": decision_node.node_id,
                **ai_provenance,
                **learning_provenance,
            },
        )
        graph.link(decision_node.node_id, recommendation_node.node_id, relation="recommends")
        return IntelligenceDecisionResult(decision=decision_node, recommendation=recommendation_node)

    @staticmethod
    def _require(graph: IntelligenceEvidenceGraph, node_id: str) -> EvidenceNode:
        snapshot = graph.snapshot()
        for row in snapshot["nodes"]:
            if row["node_id"] == node_id:
                return EvidenceNode(
                    node_id=row["node_id"],
                    node_type=row["node_type"],
                    workspace_id=row["workspace_id"],
                    payload=row["payload"],
                    provenance=row["provenance"],
                    created_at=row["created_at"],
                    schema_version=row["schema_version"],
                )
        raise ValueError(f"unknown evidence node: {node_id}")


__all__ = [
    "LearningUseMode",
    "IntelligenceDecisionPolicy",
    "IntelligenceDecisionResult",
    "decision_semantic_fingerprint",
    "IntelligenceDecisionEngine",
]
