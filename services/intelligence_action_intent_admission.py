"""Phase 5 governed ActionIntent admission boundary.

This is the canonical handoff between Intelligence Policy Governance and the
execution-facing ActionIntent model. It does not authorize or execute work.
It only admits ActionIntents carrying a valid, approved governance decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.intelligence_evidence_graph import EvidenceNode, IntelligenceEvidenceGraph
from services.intelligence_policy_governance import (
    GovernanceDisposition,
    GovernanceEvaluation,
    IntelligencePolicy,
)


@dataclass(frozen=True)
class ActionIntentAdmission:
    """Immutable evidence that an ActionIntent crossed the intelligence gate."""

    action_intent_node_id: str
    workspace_id: str
    policy_id: str
    recommendation_node_id: str
    disposition: GovernanceDisposition
    admission_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_intent_node_id": self.action_intent_node_id,
            "workspace_id": self.workspace_id,
            "policy_id": self.policy_id,
            "recommendation_node_id": self.recommendation_node_id,
            "disposition": self.disposition.value,
            "admission_fingerprint": self.admission_fingerprint,
        }


class IntelligenceActionIntentAdmissionGate:
    """Rejects any intelligence ActionIntent that is not demonstrably governed."""

    ORIGIN = "intelligence_policy_governance"

    def admit(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        action_intent_node_id: str,
        policy: IntelligencePolicy,
        evaluation: GovernanceEvaluation,
    ) -> ActionIntentAdmission:
        if evaluation.disposition is not GovernanceDisposition.APPROVED:
            raise ValueError("only APPROVED governance evaluations may be admitted")
        if not evaluation.governed:
            raise ValueError("governance evaluation is not marked governed")
        if evaluation.policy_id != policy.policy_id:
            raise ValueError("evaluation policy does not match supplied policy")
        if evaluation.workspace_id != policy.workspace_id:
            raise ValueError("evaluation workspace does not match policy")

        action = self._get_node(graph, action_intent_node_id)
        if action.node_type != "action_intent":
            raise ValueError("admission target must be an action_intent")
        if action.workspace_id != policy.workspace_id:
            raise ValueError("ActionIntent workspace does not match policy")

        governance = action.payload.get("governance")
        if not isinstance(governance, Mapping):
            raise ValueError("ActionIntent is missing governance evidence")
        if governance.get("policy_id") != policy.policy_id:
            raise ValueError("ActionIntent governance policy mismatch")
        if governance.get("disposition") != GovernanceDisposition.APPROVED.value:
            raise ValueError("ActionIntent governance disposition is not APPROVED")
        if action.provenance.get("governance_policy_id") != policy.policy_id:
            raise ValueError("ActionIntent provenance is missing governance policy")
        if action.provenance.get("governance_disposition") != GovernanceDisposition.APPROVED.value:
            raise ValueError("ActionIntent provenance is not approved")
        if action.provenance.get("recommendation_node_id") != evaluation.recommendation_node_id:
            raise ValueError("ActionIntent recommendation lineage mismatch")

        if action.payload.get("action_type") != evaluation.action_type:
            raise ValueError("ActionIntent action_type does not match governance evaluation")
        if float(action.payload.get("confidence", -1.0)) != evaluation.confidence:
            raise ValueError("ActionIntent confidence does not match governance evaluation")
        if float(action.payload.get("uncertainty", -1.0)) != evaluation.uncertainty:
            raise ValueError("ActionIntent uncertainty does not match governance evaluation")

        lineage = graph.validate_lineage(action_intent_node_id)
        if lineage["workspace_id"] != policy.workspace_id:
            raise ValueError("ActionIntent lineage workspace mismatch")

        fingerprint = action.to_dict()["fingerprint"]
        return ActionIntentAdmission(
            action_intent_node_id=action.node_id,
            workspace_id=policy.workspace_id,
            policy_id=policy.policy_id,
            recommendation_node_id=evaluation.recommendation_node_id,
            disposition=GovernanceDisposition.APPROVED,
            admission_fingerprint=fingerprint,
        )

    @staticmethod
    def _get_node(graph: IntelligenceEvidenceGraph, node_id: str) -> EvidenceNode:
        for row in graph.snapshot()["nodes"]:
            if row["node_id"] == node_id:
                return EvidenceNode(
                    node_id=row["node_id"],
                    node_type=row["node_type"],
                    workspace_id=row["workspace_id"],
                    payload=row["payload"],
                    provenance=row["provenance"],
                    created_at=row["created_at"],
                    schema_version=row["schema_version"],
                    fingerprint=row.get("fingerprint", ""),
                )
        raise ValueError(f"unknown ActionIntent node: {node_id}")
