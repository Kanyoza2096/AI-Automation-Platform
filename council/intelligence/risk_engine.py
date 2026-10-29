"""Risk Engine — independent risk calculation."""

from __future__ import annotations

from typing import Any, Dict

from council.models import Proposal, RiskLevel


class RiskEngine:
    """Calculates risk independently of personas."""
    
    # Risk keywords
    DANGEROUS_KEYWORDS = {
        "delete", "drop", "reset", "destroy", "wipe",
        "remove all", "permanently",
    }
    
    HIGH_RISK_KEYWORDS = {
        "restart", "safe_mode", "billing", "refund",
        "legal", "breaking", "access_control",
    }
    
    MEDIUM_RISK_KEYWORDS = {
        "publish", "boost", "reduce", "experiment",
        "schedule", "campaign",
    }
    
    def assess_risk(self, proposal: Proposal) -> RiskLevel:
        """Assess risk level."""
        action = proposal.action_name.lower()
        description = proposal.description.lower()
        combined = f"{action} {description}"
        
        for keyword in self.DANGEROUS_KEYWORDS:
            if keyword in combined:
                return RiskLevel.CRITICAL
        
        for keyword in self.HIGH_RISK_KEYWORDS:
            if keyword in combined:
                return RiskLevel.HIGH
        
        for keyword in self.MEDIUM_RISK_KEYWORDS:
            if keyword in combined:
                return RiskLevel.MEDIUM
        
        return RiskLevel.LOW
    
    def get_risk_score(self, proposal: Proposal) -> float:
        """Calculate numeric risk score (0.0 = safe, 1.0 = dangerous)."""
        risk_level = self.assess_risk(proposal)
        
        score_map = {
            RiskLevel.TRIVIAL: 0.0,
            RiskLevel.LOW: 0.2,
            RiskLevel.MEDIUM: 0.5,
            RiskLevel.HIGH: 0.8,
            RiskLevel.CRITICAL: 1.0,
        }
        
        return score_map.get(risk_level, 0.5)