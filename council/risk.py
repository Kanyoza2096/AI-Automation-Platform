"""Risk assessment for proposal routing."""

from __future__ import annotations

from typing import Any, Dict

from council.models import RiskLevel, Proposal


class RiskAssessor:
    """Assesses risk level of proposals."""
    
    # Risk keyword mapping
    HIGH_RISK_ACTIONS = {
        "delete", "drop", "reset", "rotate_credentials",
        "change_secrets", "disable_security", "change_billing",
        "refund", "send_legal", "deploy_breaking", "change_access",
        "restart_service", "safe_mode",
    }
    
    CRITICAL_RISK_ACTIONS = {
        "drop_database", "reset_database", "delete_client_data",
        "disable_security_controls", "change_production_secrets",
        "change_access_control",
    }
    
    MEDIUM_RISK_ACTIONS = {
        "publish_post", "boost_content", "reduce_content",
        "create_task", "queue_brand_revival",
        "optimize_content_schedule", "change_posting_time",
        "change_content_type",
    }
    
    LOW_RISK_ACTIONS = {
        "generate_post", "calculate_brand_health",
        "run_health_scan", "run_guardian_scan",
        "select_strategy", "analyze_growth",
    }
    
    TRIVIAL_ACTIONS = {
        "update_cache", "refresh_metrics", "log_event",
        "format_response", "retry_harmless",
    }
    
    def assess(self, proposal: Proposal) -> RiskLevel:
        """Assess the risk level of a proposal."""
        action = proposal.action_name.lower()
        
        # Check critical first
        for keyword in self.CRITICAL_RISK_ACTIONS:
            if keyword in action:
                return RiskLevel.CRITICAL
        
        # Check high
        for keyword in self.HIGH_RISK_ACTIONS:
            if keyword in action:
                return RiskLevel.HIGH
        
        # Check medium
        for keyword in self.MEDIUM_RISK_ACTIONS:
            if keyword in action:
                return RiskLevel.MEDIUM
        
        # Check low
        for keyword in self.LOW_RISK_ACTIONS:
            if keyword in action:
                return RiskLevel.LOW
        
        # Check trivial
        for keyword in self.TRIVIAL_ACTIONS:
            if keyword in action:
                return RiskLevel.TRIVIAL
        
        # Default
        return RiskLevel.MEDIUM