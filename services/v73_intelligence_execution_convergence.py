"""V73 intelligence-to-execution convergence admission.

The only production bridge from the V65.9 decision handoff to consequential
execution. It materializes trusted parameters, then submits exactly one durable
ActionIntent to the existing Action Kernel.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kanyoza.v73.convergence")


class IntelligenceExecutionConvergence:
    VERSION = "v73.1-v73.2.1"
    HANDOFF_EVENT = "business.decision.action.handoff.ready"

    def __init__(self, *, db=None, kernel=None, context_resolver=None):
        self.db = db
        self.kernel = kernel
        self.admitted = 0
        self.rejected = 0
        self.needs_review = 0
        self.context_resolver = context_resolver

    async def admit(self, *, event: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        from services.business_decision_action_handoff import (
            handoff_fingerprint,
            validate_action_kernel_handoff,
        )
        from services.business_action_intent_materializer import BusinessActionIntentMaterializer

        ws = str(workspace_id or "").strip()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        handoff = payload.get("business_decision_action_handoff") if isinstance(payload, dict) else None
        if not isinstance(handoff, dict):
            self.rejected += 1
            raise ValueError("business decision action handoff is missing")
        validate_action_kernel_handoff(handoff)
        if str(handoff.get("workspace_id")) != ws:
            self.rejected += 1
            raise ValueError("handoff workspace does not match event workspace")

        supplied_fp = str(payload.get("handoff_fingerprint") or "").strip() if isinstance(payload, dict) else ""
        if supplied_fp and supplied_fp != handoff_fingerprint(handoff):
            self.rejected += 1
            raise ValueError("handoff fingerprint mismatch")

        state = str(handoff.get("state") or "").strip().lower()
        if state == "blocked":
            self.rejected += 1
            return {"status": "blocked", "reason": handoff.get("reason"), "handoff_id": handoff.get("handoff_id")}

        # V65.7 policy approval is independent of the Action Kernel contract
        # risk. Never let a medium-risk kernel contract accidentally turn a
        # policy-level APPROVAL_REQUIRED disposition into autonomous execution.
        # A later human-approval path must explicitly release such a handoff.
        if state == "approval_required":
            self.needs_review += 1
            return {
                "status": "approval_required",
                "reason": handoff.get("reason") or "policy approval required before admission",
                "handoff_id": handoff.get("handoff_id"),
                "action_type": handoff.get("action_type"),
            }

        from services.business_action_context import BusinessActionContextResolver
        resolver = self.context_resolver or BusinessActionContextResolver()
        materialized = BusinessActionIntentMaterializer.materialize(handoff, context_resolver=resolver)
        if materialized.status != "ready":
            self.needs_review += 1
            return {
                "status": materialized.status,
                "reason": materialized.reason,
                "handoff_id": handoff.get("handoff_id"),
                "action_type": materialized.action_type,
            }

        from services.action_kernel import build_default_action_kernel
        kernel = self.kernel or build_default_action_kernel(self.db, allow_in_memory=self.db is None)
        result = await kernel.enqueue(
            workspace_id=ws,
            action_type=materialized.action_type,
            parameters=materialized.parameters,
            risk_level=str(handoff.get("risk_level") or "medium"),
            decision_id=str(handoff.get("evaluation_id") or "") or None,
            correlation_id=str(event.get("correlation_id") or handoff.get("parameters", {}).get("correlation_id") or "") or None,
            idempotency_key=str(handoff.get("idempotency_key") or ""),
            source="v73.intelligence_execution_convergence",
            approved=False,
        )
        self.admitted += 1
        return {
            **result,
            "handoff_id": handoff.get("handoff_id"),
            "materializer_version": BusinessActionIntentMaterializer.VERSION,
            "convergence_version": self.VERSION,
        }


intelligence_execution_convergence = IntelligenceExecutionConvergence()
