"""Risk Manager persona with hard veto power."""

from typing import Dict

from council.models import Proposal, PersonaVote, VetoType
from council.personas.base import Persona


class RiskManager(Persona):
    name = "risk_manager"
    role = "Risk Advisor"
    weight = 1.0
    hard_veto_power = True
    
    HARD_VETO_ACTIONS = {
        "drop_database", "reset_database", "delete_client_data",
        "delete_workspace", "wipe_workspace", "purge_data",
        "destroy_storage", "terminate_account", "delete_brand",
        "disable_security_controls",
    }
    
    SOFT_VETO_ACTIONS = {
        "restart_service", "safe_mode", "change_billing",
        "refund_client", "deploy_breaking_change",
    }
    
    def evaluate(self, proposal: Proposal) -> PersonaVote:
        action = proposal.action_name.lower()
        
        # Hard veto for dangerous actions
        for keyword in self.HARD_VETO_ACTIONS:
            if keyword in action:
                return self._vote_with_behavior(
                    proposal_id=proposal.id,
                    base_vote="reject",
                    reason=f"HARD VETO: Dangerous action detected: '{keyword}'.",
                    confidence=1.0,
                    veto_type=VetoType.HARD,
                )
        
        # Irreversible actions are never council-approved on a simple
        # majority. The execution layer may require a human approval, but
        # the risk member forces the proposal into escalation first.
        if any(marker in action for marker in (
            "drop_database", "reset_database", "delete_client_data",
            "delete_workspace", "wipe_workspace", "purge_data",
            "destroy_storage", "terminate_account", "delete_brand",
            "refund_client",
        )):
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="reject",
                reason="HARD VETO: Irreversible/destructive action requires explicit human approval and separate safety verification.",
                confidence=1.0,
                veto_type=VetoType.HARD,
                evidence={"category": "irreversible_action"},
            )

        # Soft veto for risky actions
        for keyword in self.SOFT_VETO_ACTIONS:
            if keyword in action:
                return self._vote_with_behavior(
                    proposal_id=proposal.id,
                    base_vote="reject",
                    reason=f"SOFT VETO: Risk-sensitive action: '{keyword}'.",
                    confidence=0.80,
                    veto_type=VetoType.SOFT,
                )
        
        # System health check
        system_health = proposal.metadata.get("system_health", 80)
        if system_health < 50:
            return self._vote_with_behavior(
                proposal_id=proposal.id,
                base_vote="reject",
                reason="System health below safe threshold.",
                confidence=0.70,
            )
        
        return self._vote_with_behavior(
            proposal_id=proposal.id,
            base_vote="abstain",
            reason="No significant risk detected.",
            confidence=0.60,
        )