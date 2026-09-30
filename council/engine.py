"""Main Democracy Engine — orchestrates all components."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from council.config import DemocracyConfig
from council.router import DecisionRouter
from council.consensus import ConsensusEngine
from council.reliability import ReliabilityTracker
from council.memory import OutcomeMemory
from council.models import (
    DeliberationResult, DecisionState, PersonaVote,
    Proposal, RiskLevel,
)
from council.personas import ALL_PERSONAS
from council.policies.governance_safety import enforce_risk_decision_boundary

logger = logging.getLogger("kanyoza.council.engine")


class DemocracyEngine:
    """Adaptive AI governance engine."""
    
    VERSION = "1.0.0"
    
    def __init__(self, config: Optional[DemocracyConfig] = None):
        self.config = config or DemocracyConfig()
        self.router = DecisionRouter(self.config)
        self.consensus = ConsensusEngine(self.config)
        self.reliability = ReliabilityTracker(self.config.minimum_samples_for_reliability)
        self.memory = OutcomeMemory()
        self._personas = {p.name: p() for p in ALL_PERSONAS}
        self._available = True
        self._cache: dict = {}
    
    @property
    def is_available(self) -> bool:
        return self._available
    
    def set_available(self, available: bool) -> None:
        self._available = available
    
    async def deliberate(self, proposal: Proposal) -> DeliberationResult:
        """Deliberate on a proposal and return decision."""
        start = time.monotonic()
        
        # Cache decisions only for the exact governance context. A decision
        # for one workspace/brand/action parameters must never be reused for
        # another tenant or materially different proposal.
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
                return enforce_risk_decision_boundary(proposal, cached["result"])
        
        # Route by risk
        tier = self.router.route(proposal)
        
        # Tier 0: Auto-approve
        if tier == 0 and proposal.risk_level == RiskLevel.TRIVIAL:
            result = DeliberationResult(
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
                deliberation_time_ms=round((time.monotonic() - start) * 1000, 3),
            )
            self._cache_result(cache_key, result)
            return result
        
        # Council unavailable — degrade gracefully
        if not self.is_available:
            if proposal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                return DeliberationResult(
                    proposal_id=proposal.id,
                    decision=DecisionState.BLOCKED,
                    approve_ratio=0.0, reject_ratio=1.0,
                    quorum_ratio=0.0, confidence=1.0,
                    votes=[], dissent=[], warnings=[],
                    risk_level=proposal.risk_level,
                    tier_used=tier,
                    fallback_used=True,
                    deliberation_time_ms=round((time.monotonic() - start) * 1000, 3),
                )
            else:
                return DeliberationResult(
                    proposal_id=proposal.id,
                    decision=DecisionState.APPROVED_WITH_WARNINGS,
                    approve_ratio=0.6, reject_ratio=0.0,
                    quorum_ratio=0.0, confidence=0.6,
                    votes=[], dissent=[], warnings=["Council unavailable — auto-approved with warnings"],
                    risk_level=proposal.risk_level,
                    tier_used=tier,
                    fallback_used=True,
                    deliberation_time_ms=round((time.monotonic() - start) * 1000, 3),
                )
        
        # Select agents
        selected_names = self.router.select_agents(proposal, tier)
        personas = self._get_personas(selected_names, tier)
        
        # Vote
        votes: list[PersonaVote] = []
        for persona in personas:
            try:
                vote = persona.evaluate(proposal)
                vote.reliability = self.reliability.get_reliability(persona.name)
                votes.append(vote)
                
                # High/critical decisions require the complete deliberation
                # panel. Early termination is acceptable only for lower-risk
                # proposals; stopping early can otherwise manufacture a false
                # consensus before safety/evidence voices have spoken.
                if (
                    self.config.enable_early_stop
                    and proposal.risk_level not in (RiskLevel.HIGH, RiskLevel.CRITICAL)
                    and self._should_stop_early(votes)
                ):
                    break
            except Exception as exc:
                logger.warning("Persona %s failed: %s", persona.name, exc)
        
        # Consensus
        result = self.consensus.deliberate(proposal, votes, tier)
        
        result = enforce_risk_decision_boundary(proposal, result)

        # Cache
        self._cache_result(cache_key, result)
        
        return result
    
    def _get_personas(self, selected_names: Optional[list], tier: int) -> list:
        """Get personas for voting."""
        if selected_names:
            return [self._personas[name] for name in selected_names if name in self._personas]
        
        # Use all personas, limited by tier
        all_personas = list(self._personas.values())
        if tier == 1:
            return all_personas[:self.config.tier_1_persona_count]
        elif tier == 2:
            return all_personas[:self.config.tier_2_persona_count]
        else:
            return all_personas
    
    def _should_stop_early(self, votes: list) -> bool:
        """Check if deliberation should terminate early."""
        if len(votes) < 2:
            return False
        
        approve = sum(v.confidence for v in votes if v.vote == "approve")
        reject = sum(v.confidence for v in votes if v.vote == "reject")
        total = approve + reject
        
        if total == 0:
            return False
        
        approve_ratio = approve / total
        reject_ratio = reject / total
        
        margin = self.config.early_stop_confidence_margin
        
        # Stop if one side clearly dominates
        if approve_ratio > 0.75 + margin:
            return True
        if reject_ratio > 0.75 + margin:
            return True
        
        # Hard veto stops immediately
        for vote in votes:
            if hasattr(vote, 'veto_type') and vote.veto_type.value == "hard" and vote.vote == "reject":
                return True
        
        return False
    
    def _cache_result(self, key: str, result: DeliberationResult) -> None:
        if self.config.enable_result_cache:
            self._cache[key] = {
                "result": result,
                "cached_at": time.monotonic(),
            }
    
    def record_outcome(self, proposal_id: str, decision: DecisionState, actual_outcome: str, success_metric: float = 0.5) -> None:
        """Record real-world outcome for learning."""
        self.memory.record(proposal_id, decision, actual_outcome, success_metric)
        
        # Update persona reliability (simplified)
        for vote in self._cache.get(proposal_id, {}).get("votes", []):
            self.reliability.record_outcome(vote.member, vote.vote, actual_outcome)
    
    def get_status(self) -> dict:
        """Get Council status."""
        return {
            "version": self.VERSION,
            "available": self.is_available,
            "personas": len(self._personas),
            "reliability": self.reliability.get_all(),
            "cache_size": len(self._cache),
            "memory_size": self.memory.size,
        }