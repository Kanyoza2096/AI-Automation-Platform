"""
Kanyoza Reflex System v1.0
===========================

Instant responses to critical events — no deliberation, no voting.
Like pulling your hand away from fire.

REFLEXES:
  - DANGEROUS_ACTION → INSTANT BLOCK
  - SERVICE_CRASH → INSTANT ISOLATION
  - SECURITY_BREACH → INSTANT LOCKDOWN
  - DATABASE_FAILURE → INSTANT CACHE MODE
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.reflexes")


class ReflexType(str, Enum):
    BLOCK_DANGEROUS_ACTION = "block_dangerous_action"
    ISOLATE_FAILED_SERVICE = "isolate_failed_service"
    LOCKDOWN_SECURITY = "lockdown_security"
    CACHE_MODE = "cache_mode"


class ReflexSystem:
    """
    Executes instant responses to critical events.
    
    Unlike the Council (which deliberates), reflexes act IMMEDIATELY.

    Phase A: SPECIES_LEVEL — tenants cannot disable or vote down reflexes.
    """
    
    VERSION = "1.1.0"
    SPECIES_LEVEL = True  # non-negotiable species law
    CAN_DISABLE = False
    
    # Actions that trigger instant blocking (no Council vote needed)
    INSTANT_BLOCK_ACTIONS = {
        "drop_database",
        "delete_client_data",
        "reset_database",
        "disable_security_controls",
        "change_production_secrets",
        "send_legal_notice",
        "change_access_control",
    }
    
    # Actions that trigger instant lockdown
    SECURITY_BREACH_PATTERNS = {
        "unauthorized_access",
        "credential_leak",
        "token_exposure",
        "suspicious_activity",
    }
    
    def __init__(self, event_bus=None, container=None):
        self._event_bus = event_bus
        self._container = container
        self._triggered_reflexes: List[Dict[str, Any]] = []
        self._locked_down = False
    
    @property
    def service_name(self) -> str:
        return "reflex_system"
    
    async def evaluate_action(self, action_name: str, action_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if an action triggers an instant reflex.
        
        Returns:
            {"blocked": True/False, "reason": "..."}
        """
        action_lower = action_name.lower()
        
        # Reflex 1: Instant block dangerous actions
        for keyword in self.INSTANT_BLOCK_ACTIONS:
            if keyword in action_lower:
                await self._trigger_reflex(
                    ReflexType.BLOCK_DANGEROUS_ACTION,
                    action_name=action_name,
                    reason=f"Dangerous action '{keyword}' blocked instantly",
                )
                return {"blocked": True, "reason": f"REFLEX: Dangerous action '{keyword}' blocked"}
        
        # Reflex 2: Security breach detection
        combined = f"{action_lower} {json.dumps(action_params, default=str).lower()}"
        for pattern in self.SECURITY_BREACH_PATTERNS:
            if pattern in combined:
                await self._trigger_reflex(
                    ReflexType.LOCKDOWN_SECURITY,
                    action_name=action_name,
                    reason=f"Security breach pattern detected: '{pattern}'",
                )
                self._locked_down = True
                return {"blocked": True, "reason": f"REFLEX: Security lockdown triggered"}
        
        return {"blocked": False, "reason": None}
    
    async def on_service_crash(self, service_name: str, error: str) -> None:
        """Reflex: Isolate failed service immediately."""
        await self._trigger_reflex(
            ReflexType.ISOLATE_FAILED_SERVICE,
            action_name=f"service_crash:{service_name}",
            reason=f"Service '{service_name}' crashed: {error[:200]}",
        )
        logger.critical("⚡ REFLEX: Isolating %s", service_name)
    
    async def on_database_failure(self, error: str) -> None:
        """Reflex: Switch to cache mode immediately."""
        await self._trigger_reflex(
            ReflexType.CACHE_MODE,
            action_name="database_failure",
            reason=f"Database failure: {error[:200]}",
        )
        logger.critical("⚡ REFLEX: Switching to cache mode")
    
    async def _trigger_reflex(self, reflex_type: ReflexType, action_name: str, reason: str) -> None:
        """Record and publish a reflex event."""
        reflex_event = {
            "id": f"reflex_{int(time.time() * 1000)}",
            "type": reflex_type.value,
            "action_name": action_name,
            "reason": reason,
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "instant": True,  # No deliberation happened
        }
        
        self._triggered_reflexes.append(reflex_event)
        self._triggered_reflexes = self._triggered_reflexes[-50:]  # Keep last 50
        
        # Publish to event bus
        bus = self._event_bus
        if bus is None and self._container:
            from core.unified_bus import UnifiedEventBus
            bus = self._container.try_resolve(UnifiedEventBus)
        
        if bus:
            try:
                await bus.publish(
                    "reflex.triggered",
                    reflex_event,
                    source="reflex_system",
                )
            except Exception:
                pass
    
    @property
    def is_locked_down(self) -> bool:
        return self._locked_down
    
    def get_status(self) -> Dict[str, Any]:
        """Get reflex system status."""
        return {
            "version": self.VERSION,
            "locked_down": self._locked_down,
            "total_reflexes_triggered": len(self._triggered_reflexes),
            "recent_reflexes": self._triggered_reflexes[-10:],
        }


# Global reflex system
reflex_system = ReflexSystem()


def get_reflex_system() -> ReflexSystem:
    return reflex_system


__all__ = [
    "ReflexSystem",
    "ReflexType",
    "reflex_system",
    "get_reflex_system",
]