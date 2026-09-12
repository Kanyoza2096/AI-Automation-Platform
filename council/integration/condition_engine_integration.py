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
            return {"success": False, "escalated": True, "reason": decision.reason}
        
        # Execute approved action
        try:
            if action in {"increase_similar_content", "reduce_underperforming_content", "reduce_content_risk"}:
                # Condition Engine decisions are advisory until the action crosses
                # this governed execution boundary. This gives every autonomous
                # content mutation a durable idempotency/lease/receipt contract.
                db = getattr(condition_engine, "supabase", None)
                ledger = ExecutionLedger(db=db, allow_in_memory=db is None)
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
                # Normalize the canonical success field so the Condition Engine
                # can distinguish execution from merely returning a payload.
                if "ok" not in result:
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