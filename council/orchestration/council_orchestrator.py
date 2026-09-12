"""
Council Orchestrator — Routes proposals to appropriate path.

This is the MAIN entry point for the Democracy Engine.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from council.config import DemocracyConfig
from council.intelligence.decision_router import DecisionRouter
from council.intelligence.risk_engine import RiskEngine
from council.orchestration.fast_path import FastPath
from council.orchestration.standard_path import StandardPath
from council.orchestration.deep_deliberation import DeepDeliberation
from council.orchestration.fallback_strategy import FallbackStrategy
from council.models import (
    DeliberationResult, DecisionState, Proposal, RiskLevel,
)
from council.reliability import ReliabilityTracker
from council.memory import OutcomeMemory
from council.personas import ALL_PERSONAS

logger = logging.getLogger("kanyoza.council.orchestrator")


class CouncilOrchestrator:
    """Main orchestrator for the Democracy Engine."""
    
    VERSION = "1.0.0"
    
    def __init__(self, config: Optional[DemocracyConfig] = None):
        self.config = config or DemocracyConfig()
        self.router = DecisionRouter(self.config)
        self.risk_engine = RiskEngine()
        self.reliability = ReliabilityTracker(self.config.minimum_samples_for_reliability)
        self.memory = OutcomeMemory()
        
        self._personas = {p.name: p() for p in ALL_PERSONAS}
        self._available = True
        
        self.fast_path = FastPath(self._personas, self.config)
        self.standard_path = StandardPath(self._personas, self.config)
        self.deep_deliberation = DeepDeliberation(self._personas, self.reliability, self.config)
        self.fallback = FallbackStrategy(self.config)
        
        self._cache: dict = {}
    
    @property
    def is_available(self) -> bool:
        return self._available
    
    def set_available(self, available: bool) -> None:
        self._available = available
        logger.warning("Council availability set to: %s", available)
    
    async def deliberate(self, proposal: Proposal) -> DeliberationResult:
        """Main deliberation entry point."""
        start = time.monotonic()
        
        # Cache only exact governance contexts; never reuse a decision across
        # workspaces, brands, parameters, or risk levels.
        import hashlib, json
        cache_context = {
            "workspace_id": proposal.metadata.get("workspace_id"),
            "brand_id": proposal.metadata.get("brand_id"),
            "action_params": proposal.action_params,
            "metadata": proposal.metadata,
            "risk_level": str(proposal.risk_level.value if hasattr(proposal.risk_level, "value") else proposal.risk_level),
        }
        cache_blob = json.dumps(cache_context, sort_keys=True, default=str, separators=(",", ":"))
        cache_digest = hashlib.sha256(cache_blob.encode()).hexdigest()[:20]
        cache_key = f"{proposal.action_name}:{proposal.source_engine}:{proposal.proposal_type}:{cache_digest}"
        if self.config.enable_result_cache and cache_key in self._cache:
            cached = self._cache[cache_key]
            if time.monotonic() - cached["cached_at"] < self.config.cache_ttl_seconds:
                logger.debug("Cache hit for %s", cache_key)
                return cached["result"]
        
        # Route through the conservative risk boundary first. The router
        # may raise a caller-supplied risk classification but never lower it.
        tier = self.router.route(proposal)

        # Destructive/critical actions are never auto-approved and never use
        # the fast path. Human approval is a separate execution gate; Council
        # must first establish a defensible recommendation.
        if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            tier = 3
        
        # Council unavailable — use fallback
        if not self.is_available:
            result = self.fallback.evaluate(proposal, tier)
            return result
        
        # Select agents
        selected_agents = self.router.select_agents(proposal, tier)
        
        # Execute appropriate path
        if tier == 0:
            result = self._auto_approve(proposal)
        elif tier == 1:
            result = await self.fast_path.deliberate(proposal, selected_agents)
        elif tier == 2:
            result = await self.standard_path.deliberate(proposal, selected_agents)
        else:
            result = await self.deep_deliberation.deliberate(proposal, selected_agents)
        
        # Cache only terminal governance results. Do not cache pending or
        # revision states because evidence/context may change immediately.
        if self.config.enable_result_cache and result.decision not in (
            DecisionState.NEEDS_REVISION, DecisionState.ESCALATED, DecisionState.PENDING
        ):
            self._cache[cache_key] = {
                "result": result,
                "cached_at": time.monotonic(),
            }
        
        logger.info(
            "Deliberation complete: %s → %s (tier=%d, %.1fms)",
            proposal.action_name,
            result.decision.value,
            tier,
            result.deliberation_time_ms,
        )
        
        return result
    
    def _auto_approve(self, proposal: Proposal) -> DeliberationResult:
        """Auto-approve trivial actions."""
        return DeliberationResult(
            proposal_id=proposal.id,
            decision=DecisionState.APPROVED,
            approve_ratio=1.0,
            reject_ratio=0.0,
            quorum_ratio=1.0,
            confidence=1.0,
            votes=[],
            dissent=[],
            warnings=[],
            risk_level=RiskLevel.TRIVIAL,
            tier_used=0,
        )
    
    def record_outcome(
        self,
        proposal_id: str,
        decision: DecisionState,
        actual_outcome: str,
        success_metric: float = 0.5,
    ) -> None:
        """Record real-world outcome for learning."""
        self.memory.record(proposal_id, decision, actual_outcome, success_metric)
    
    def get_status(self) -> dict:
        """Get orchestrator status."""
        return {
            "version": self.VERSION,
            "available": self.is_available,
            "personas": len(self._personas),
            "persona_names": list(self._personas.keys()),
            "reliability": self.reliability.get_all(),
            "cache_size": len(self._cache),
            "memory_size": self.memory.size,
        }