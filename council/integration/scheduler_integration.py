"""
Scheduler Integration with Democracy Engine.
Every publish requires Council approval.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from council.integration import deliberate_action

logger = logging.getLogger("kanyoza.integration.scheduler")


def bind_scheduler_with_council(scheduler_manager) -> None:
    """Wrap scheduler publish with Council approval."""
    
    original_trigger = scheduler_manager.trigger_for_brand
    
    async def council_approved_trigger(brand_id: str, idempotency_key: str = None) -> dict:
        """Trigger with Council approval."""
        
        decision = await deliberate_action(
            action_name="publish_content",
            source_engine="brand_scheduler",
            description=f"Publish scheduled content for brand {brand_id}",
            proposal_type="content_creation",
            metadata={
                "brand_id": brand_id,
                "system_health": 80,
            },
        )
        
        if decision.is_blocked:
            return {"ok": False, "blocked": True, "reason": decision.reason}
        
        if decision.needs_escalation:
            return {"ok": False, "escalated": True, "reason": decision.reason}
        
        result = await original_trigger(brand_id, idempotency_key)
        if isinstance(result, dict):
            result["council"] = decision.to_dict()
        return result
    
    scheduler_manager.trigger_for_brand = council_approved_trigger
    logger.info("✅ Scheduler bound with Council approval")