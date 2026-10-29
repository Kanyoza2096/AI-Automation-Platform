"""
Condition Engine Integration with Democracy Engine.
Every content action requires Council approval.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from council.integration import require_council, deliberate_action
from services.execution_ledger import ExecutionLedger
from services.external_action_executor import ExternalActionExecutor

logger = logging.getLogger("kanyoza.integration.condition")


def bind_condition_engine_with_council(condition_engine) -> None:
    """Bind Condition Engine executor with Council approval."""
    
    async def council_approved_executor(plan) -> Dict[str, Any]:
        """Execute plan with Council approval."""
        from services.content_engine import content_engine
        
        action = plan.action
        brand_id = plan.brand_id
        params = plan.parameters or {}
        
        # Build proposal for Council
        decision = await deliberate_action(
            action_name=action,
            source_engine="condition_engine",
            description=plan.reason or f"Execute {action}",
            proposal_type=_get_proposal_type(action),
            metadata={
                "brand_id": brand_id,
                "engagement_ratio": params.get("engagement_ratio", 1.0),
                "system_health": params.get("system_health", 80),
            },
            action_params=params,
        )
        
        if decision.is_blocked:
            logger.warning("Council blocked %s: %s", action, decision.reason)
            return {"success": False, "blocked": True, "reason": decision.reason}
        
        if decision.needs_escalation:
            logger.warning("Council escalated %s", action)
            return {"ok": False, "success": False, "escalated": True, "reason": decision.reason,
                    "council": decision.to_dict()}

        # Fail closed unless the Council adapter explicitly confirms an
        # approval state. A missing/unknown decision is not implicit consent.
        if not bool(getattr(decision, "is_approved", False)):
            logger.warning("Council approval not confirmed for %s", action)
            return {
                "ok": False,
                "success": False,
                "governance_unverified": True,
                "error": "council_approval_not_confirmed",
                "reason": getattr(decision, "reason", "Council returned no confirmed approval"),
                "council": decision.to_dict() if callable(getattr(decision, "to_dict", None)) else {"decision": "unknown"},
            }

        # Execute approved action
        try:
            if action in {"increase_similar_content", "reduce_underperforming_content", "reduce_content_risk"}:
                # Condition Engine decisions are advisory until the action crosses
                # this governed execution boundary. This gives every autonomous
                # content mutation a durable idempotency/lease/receipt contract.
                db = getattr(condition_engine, "supabase", None)
                # Consequential content mutations require a durable ledger. An
                # in-memory ledger cannot protect against duplicate effects after
                # process restart, so never silently downgrade to local memory.
                if db is None:
                    return {
                        "ok": False,
                        "success": False,
                        "execution_unavailable": True,
                        "error": "durable_execution_ledger_unavailable",
                        "council": decision.to_dict(),
                    }
                ledger = ExecutionLedger(db=db, allow_in_memory=False)
                executor = ExternalActionExecutor(ledger)
                decision_id = str(getattr(plan, "decision_id", "") or "")
                idempotency_key = ExecutionLedger.make_idempotency_key(
                    "condition", str(brand_id), action, decision_id,
                    str(params.get("duration_posts", 5)),
                    str(params.get("content_mix_change", "")),
                )

                async def perform_content_mutation():
                    if action == "increase_similar_content":
                        return await content_engine.boost_content(
                            brand_id=brand_id,
                            factor=params.get("content_mix_change", 0.20),
                            duration_posts=params.get("duration_posts", 5),
                        )
                    if action == "reduce_underperforming_content":
                        return await content_engine.reduce_content(
                            brand_id=brand_id,
                            factor=params.get("content_mix_change", -0.20),
                            duration_posts=params.get("duration_posts", 5),
                        )
                    return await content_engine.safe_mode(
                        brand_id=brand_id,
                        duration_posts=params.get("duration_posts", 5),
                    )

                if not params.get("workspace_id"):
                    return {"ok": False, "success": False, "error": "workspace_id_required_for_governed_execution"}
                result = await executor.execute(
                    workspace_id=str(params.get("workspace_id") or ""),
                    action=f"condition.{action}",
                    idempotency_key=idempotency_key,
                    provider="content_engine",
                    operation=perform_content_mutation,
                    metadata={
                        "brand_id": brand_id,
                        "decision_id": decision_id,
                        "risk": str(getattr(plan, "risk", "low")),
                        "confidence": float(getattr(plan, "confidence", 0.0) or 0.0),
                    },
                )
                if result.get("status") == "uncertain":
                    result["reconciliation_required"] = True
            else:
                # A capability that has no concrete executor must not masquerade
                # as a successful autonomous action. Keep the decision auditable
                # and fail closed until a real executor is wired.
                return {
                    "ok": False,
                    "success": False,
                    "unsupported": True,
                    "action": action,
                    "error": f"no_executor_for_action:{action}",
                    "council": decision.to_dict(),
                }

            if isinstance(result, dict):
                # A governed execution is successful only when the executor
                # returns a terminal succeeded state and a provider object ID.
                # Do not infer success from an arbitrary payload or `ok=True`.
                if result.get("status") == "succeeded" and result.get("provider_object_id"):
                    result["ok"] = True
                    result["success"] = True
                elif result.get("status") == "succeeded":
                    result["ok"] = False
                    result["success"] = False
                    result["status"] = "uncertain"
                    result["reconciliation_required"] = True
                    result["error"] = result.get("error") or "succeeded_status_missing_provider_object_id"
                elif result.get("status") in {"uncertain", "in_progress"}:
                    result["ok"] = False
                    result["success"] = False
                    result["reconciliation_required"] = result.get("status") == "uncertain"
                elif "ok" not in result:
                    result["ok"] = bool(result.get("success", False))
                result["council"] = decision.to_dict()
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}
    
    condition_engine.set_action_executor(council_approved_executor)
    logger.info("✅ Condition Engine bound with Council approval")


def _get_proposal_type(action: str) -> str:
    """Map action to proposal type."""
    mapping = {
        "increase_similar_content": "campaign_launch",
        "reduce_underperforming_content": "content_creation",
        "reduce_content_risk": "security_action",
        "run_experiment": "campaign_launch",
        "change_posting_time": "content_creation",
        "change_content_type": "content_creation",
    }
    return mapping.get(action, "general")