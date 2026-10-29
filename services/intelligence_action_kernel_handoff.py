"""Phase 5 intelligence -> canonical authorization -> Action Kernel handoff.

This module is intentionally a handoff boundary, not a second execution engine.
An intelligence ActionIntent must first carry a valid Phase 5 admission. The
canonical KDO request pipeline then performs normal authorization/decision work
without executing a side effect. Only an allowed canonical decision is passed
to the existing Action Kernel, which owns durable action admission and execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from core.pipeline import run_action_pipeline
from core.request_context import RequestContext
from services.action_kernel import ActionKernel
from services.intelligence_action_intent_admission import ActionIntentAdmission, IntelligenceActionIntentAdmissionGate
from services.intelligence_evidence_graph import EvidenceNode, IntelligenceEvidenceGraph


@dataclass(frozen=True)
class CanonicalIntelligenceHandoff:
    action_intent_node_id: str
    admission_fingerprint: str
    workspace_id: str
    action_type: str
    decision_id: str
    idempotency_key: str
    kernel_intent_id: str
    status: str
    kernel_result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_intent_node_id": self.action_intent_node_id,
            "admission_fingerprint": self.admission_fingerprint,
            "workspace_id": self.workspace_id,
            "action_type": self.action_type,
            "decision_id": self.decision_id,
            "idempotency_key": self.idempotency_key,
            "kernel_intent_id": self.kernel_intent_id,
            "status": self.status,
            "kernel_result": dict(self.kernel_result),
        }


class IntelligenceActionKernelHandoff:
    """Crosses exactly one boundary from admitted intelligence into core execution."""

    VERSION = "phase5.1"
    SOURCE = "phase5.intelligence_action_kernel_handoff"

    def __init__(self, *, kernel: Optional[ActionKernel] = None, db=None):
        self.kernel = kernel
        self.db = db

    async def handoff(
        self,
        *,
        graph: IntelligenceEvidenceGraph,
        admission: ActionIntentAdmission,
        action_intent_node_id: str,
        policy: Any,
        evaluation: Any,
        request_context: RequestContext,
    ) -> CanonicalIntelligenceHandoff:
        """Authorize through the canonical pipeline, then enqueue once in the kernel."""
        action = self._get_action(graph, action_intent_node_id)
        self._validate_admission_binding(action, admission, request_context)
        # Re-validate the admission against the authoritative graph/policy/evaluation.
        # A caller cannot forge an ActionIntentAdmission dataclass and use it as authority.
        IntelligenceActionIntentAdmissionGate().admit(
            graph=graph, action_intent_node_id=action_intent_node_id, policy=policy, evaluation=evaluation
        )

        parameters = action.payload.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise ValueError("intelligence ActionIntent.parameters must be a mapping")
        parameters = dict(parameters)

        risk_level = str(action.payload.get("risk_level") or action.payload.get("risk_class") or "low").lower()
        if risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid ActionIntent risk level")

        # Canonical governance/authorization. This is decision-only: enqueue=False
        # and no domain hook means this call cannot perform the external side effect.
        decision = run_action_pipeline(
            action=str(action.payload.get("action_type") or ""),
            payload={"action_type": action.payload.get("action_type"), **parameters},
            permission="write",
            ctx=request_context,
            enqueue=False,
            domain_execute=None,
        )
        if not decision.allowed:
            raise PermissionError(f"canonical authorization rejected intelligence ActionIntent: {decision.reason}")
        if decision.workspace_id != request_context.workspace_id:
            raise PermissionError("canonical authorization workspace mismatch")

        kernel = self.kernel
        if kernel is None:
            from services.action_kernel import build_default_action_kernel
            kernel = build_default_action_kernel(self.db, allow_in_memory=self.db is None)

        result = await kernel.enqueue(
            workspace_id=request_context.workspace_id,
            action_type=str(action.payload.get("action_type") or ""),
            parameters={**parameters, "_kdo_intelligence": {
                "action_intent_node_id": action.node_id,
                "admission_fingerprint": admission.admission_fingerprint,
                "policy_id": admission.policy_id,
                "recommendation_node_id": admission.recommendation_node_id,
            }},
            risk_level=risk_level,
            decision_id=decision.decision_id,
            correlation_id=request_context.correlation_id or None,
            idempotency_key=str(action.payload.get("idempotency_key") or ""),
            source=self.SOURCE,
        )
        if not result.get("intent_id"):
            raise RuntimeError("Action Kernel did not return an authoritative intent_id")

        return CanonicalIntelligenceHandoff(
            action_intent_node_id=action.node_id,
            admission_fingerprint=admission.admission_fingerprint,
            workspace_id=request_context.workspace_id,
            action_type=str(action.payload.get("action_type") or ""),
            decision_id=decision.decision_id,
            idempotency_key=str(action.payload.get("idempotency_key") or ""),
            kernel_intent_id=str(result["intent_id"]),
            status=str(result.get("status") or "unknown"),
            kernel_result={k: result[k] for k in result if k in {"ok", "status", "intent_id", "action_type", "idempotency_key", "accepted"}},
        )

    @staticmethod
    def _get_action(graph: IntelligenceEvidenceGraph, node_id: str) -> EvidenceNode:
        row = next((n for n in graph.snapshot()["nodes"] if n["node_id"] == node_id), None)
        if not row or row["node_type"] != "action_intent":
            raise ValueError("unknown intelligence ActionIntent")
        return EvidenceNode(
            node_id=row["node_id"], node_type=row["node_type"], workspace_id=row["workspace_id"],
            payload=row["payload"], provenance=row["provenance"], created_at=row["created_at"],
            schema_version=row["schema_version"], fingerprint=row.get("fingerprint", ""),
        )

    @staticmethod
    def _validate_admission_binding(
        action: EvidenceNode,
        admission: ActionIntentAdmission,
        request_context: RequestContext,
    ) -> None:
        if admission.disposition.value != "APPROVED":
            raise PermissionError("only approved intelligence admissions may cross the execution handoff")
        if admission.action_intent_node_id != action.node_id:
            raise ValueError("admission ActionIntent identity mismatch")
        if admission.workspace_id != action.workspace_id or request_context.workspace_id != action.workspace_id:
            raise PermissionError("intelligence ActionIntent workspace mismatch")
        if admission.admission_fingerprint != action.to_dict()["fingerprint"]:
            raise ValueError("intelligence admission fingerprint mismatch")
        if not str(action.payload.get("action_type") or "").strip():
            raise ValueError("ActionIntent action_type is required")
        if not str(action.payload.get("idempotency_key") or "").strip():
            raise ValueError("ActionIntent idempotency_key is required")


__all__ = ["CanonicalIntelligenceHandoff", "IntelligenceActionKernelHandoff"]
