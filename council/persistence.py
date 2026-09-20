"""
Council Persistence Layer
=========================

Persists decisions, votes, and outcomes to Supabase.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("kanyoza.council.persistence")


class CouncilPersistence:
    """Persists council data to Supabase."""
    
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
    
    async def save_decision(self, decision_data: Dict[str, Any]) -> bool:
        """Save a decision."""
        if not self.supabase:
            return False
        
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.supabase.table("council_decisions").upsert({
                    "decision_id": decision_data.get("decision_id"),
                    "action": decision_data.get("action"),
                    "status": decision_data.get("status"),
                    "result": json.dumps(decision_data.get("result", {}), default=str),
                    "created_at": decision_data.get("created_at"),
                }).execute(),
            )
            return True
        except Exception as exc:
            logger.warning("Failed to save decision: %s", exc)
            return False
    
    async def save_votes(self, decision_id: str, votes: List[Dict[str, Any]]) -> bool:
        """Save votes for a decision."""
        if not self.supabase:
            return False
        
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.supabase.table("council_votes").upsert([
                    {
                        "decision_id": decision_id,
                        "member": vote.get("member"),
                        "vote": vote.get("vote"),
                        "confidence": vote.get("confidence"),
                        "reason": vote.get("reason"),
                    }
                    for vote in votes
                ]).execute(),
            )
            return True
        except Exception as exc:
            logger.warning("Failed to save votes: %s", exc)
            return False
    
    async def save_outcome(self, decision_id: str, outcome: Dict[str, Any]) -> bool:
        """Save outcome for a decision."""
        if not self.supabase:
            return False
        
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.supabase.table("council_outcomes").upsert({
                    "decision_id": decision_id,
                    "actual_outcome": outcome.get("actual_outcome"),
                    "success_metric": outcome.get("success_metric"),
                    "notes": outcome.get("notes"),
                    "recorded_at": outcome.get("recorded_at"),
                }).execute(),
            )
            return True
        except Exception as exc:
            logger.warning("Failed to save outcome: %s", exc)
            return False