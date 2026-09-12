"""
Director Integration with Democracy Engine.
Every Director task requires Council approval.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from council.integration import require_council, deliberate_action

logger = logging.getLogger("kanyoza.integration.director")


class DirectorCouncilIntegration:
    """Wires Council into Director's task execution."""
    
    def __init__(self, director):
        self.director = director
        self._original_handlers = {}
        self._bind_capabilities()
    
    def _bind_capabilities(self) -> None:
        """Wrap Director capabilities with Council approval."""
        
        # Preserve the real production handlers. Council is an authorization
        # layer; it must not replace the actual "hands" with advisory stubs.
        for name in (
            "publish_scheduled_content",
            "optimize_content_schedule",
            "calculate_brand_health",
            "queue_brand_revival",
            "restart_failed_service",
        ):
            cap = getattr(self.director, "capabilities", {}).get(name)
            if cap is not None and callable(getattr(cap, "handler", None)):
                self._original_handlers[name] = cap.handler

        # Content actions
        self.director.bind_capability(
            "publish_scheduled_content",
            self._council_approved_publish,
        )
        self.director.bind_capability(
            "optimize_content_schedule",
            self._council_approved_optimize,
        )
        self.director.bind_capability(
            "calculate_brand_health",
            self._council_approved_health_check,
        )
        self.director.bind_capability(
            "queue_brand_revival",
            self._council_approved_revival,
        )
        self.director.bind_capability(
            "restart_failed_service",
            self._council_approved_restart,
        )
    
    async def _execute_original(self, capability: str, params: Dict[str, Any]):
        handler = self._original_handlers.get(capability)
        if handler is None:
            return {"ok": False, "error": f"production_handler_unavailable:{capability}"}
        result = handler(params)
        import inspect
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _council_approved_publish(self, params: Dict[str, Any]):
        """Authorize, then invoke the real Director publishing capability."""
        decision = await deliberate_action(
            action_name="publish_content",
            source_engine="director",
            description=f"Publish content for brand {params.get('brand_id', 'default')}",
            proposal_type="content_creation",
            metadata={
                "brand_id": params.get("brand_id"),
                "topic": params.get("topic"),
                "engagement_ratio": params.get("engagement_ratio", 1.0),
                "system_health": params.get("system_health", 80),
            },
            action_params=params,
        )
        if decision.is_blocked or decision.needs_escalation:
            return {"ok": False, "blocked": decision.is_blocked, "escalated": decision.needs_escalation, "reason": decision.reason}
        result = await self._execute_original("publish_scheduled_content", params)
        if isinstance(result, dict):
            result["council"] = decision.to_dict()
        return result

    async def _council_approved_optimize(self, params: Dict[str, Any]):
        """Authorize, then invoke the real Director optimization capability."""
        decision = await deliberate_action(
            action_name="optimize_content_schedule",
            source_engine="director",
            description="Optimize content schedule",
            proposal_type="content_creation",
            metadata={"brand_id": params.get("brand_id")},
            action_params=params,
        )
        if decision.is_blocked or decision.needs_escalation:
            return {"ok": False, "blocked": decision.is_blocked, "escalated": decision.needs_escalation, "reason": decision.reason}
        result = await self._execute_original("optimize_content_schedule", params)
        if isinstance(result, dict):
            result["council"] = decision.to_dict()
        return result

    async def _council_approved_health_check(self, params: Dict[str, Any]):
        """Health observation is read-only; no Council gate is needed."""
        from services.condition_engine import autonomous_engine
        return await autonomous_engine.calculate_brand_health(params.get("brand_id", "default"))

    async def _council_approved_revival(self, params: Dict[str, Any]):
        """Authorize, then invoke the real Director revival capability."""
        decision = await deliberate_action(
            action_name="queue_brand_revival",
            source_engine="director",
            description=f"Revive brand {params.get('brand_id')}",
            proposal_type="campaign_launch",
            metadata={"brand_id": params.get("brand_id")},
            action_params=params,
        )
        if decision.is_blocked or decision.needs_escalation:
            return {"ok": False, "blocked": decision.is_blocked, "escalated": decision.needs_escalation, "reason": decision.reason}
        result = await self._execute_original("queue_brand_revival", params)
        if isinstance(result, dict):
            result["council"] = decision.to_dict()
        return result

    async def _council_approved_restart(self, params: Dict[str, Any]):
        """Authorize, then invoke the real Director recovery capability."""
        decision = await deliberate_action(
            action_name="restart_service",
            source_engine="director",
            description=f"Restart service: {params.get('service_name')}",
            proposal_type="security_action",
            metadata={
                "service_name": params.get("service_name"),
                "system_health": params.get("system_health", 80),
            },
            action_params=params,
        )
        if decision.is_blocked or decision.needs_escalation:
            return {"ok": False, "blocked": decision.is_blocked, "escalated": decision.needs_escalation, "reason": decision.reason}
        result = await self._execute_original("restart_failed_service", params)
        if isinstance(result, dict):
            result["council"] = decision.to_dict()
        return result

