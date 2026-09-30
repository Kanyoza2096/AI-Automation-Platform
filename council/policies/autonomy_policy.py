"""Autonomy policy — defines what AI can/cannot do."""

from enum import Enum


class AutonomyLevel(str, Enum):
    FULLY_AUTONOMOUS = "fully_autonomous"
    COUNCIL_APPROVAL = "council_approval"
    HUMAN_APPROVAL = "human_approval"
    FORBIDDEN = "forbidden"


class AutonomyPolicy:
    """Defines autonomy levels for system actions."""
    
    AUTONOMY_MAP = {
        # Fully autonomous
        "generate_draft": AutonomyLevel.FULLY_AUTONOMOUS,
        "analyze_performance": AutonomyLevel.FULLY_AUTONOMOUS,
        "calculate_health": AutonomyLevel.FULLY_AUTONOMOUS,
        "refresh_metrics": AutonomyLevel.FULLY_AUTONOMOUS,
        "update_cache": AutonomyLevel.FULLY_AUTONOMOUS,
        
        # Council approval required
        "publish_content": AutonomyLevel.COUNCIL_APPROVAL,
        "boost_content": AutonomyLevel.COUNCIL_APPROVAL,
        "reduce_content": AutonomyLevel.COUNCIL_APPROVAL,
        "change_posting_time": AutonomyLevel.COUNCIL_APPROVAL,
        "run_experiment": AutonomyLevel.COUNCIL_APPROVAL,
        "restart_service": AutonomyLevel.COUNCIL_APPROVAL,
        
        # Human approval required
        "rotate_credentials": AutonomyLevel.HUMAN_APPROVAL,
        "change_production_secrets": AutonomyLevel.HUMAN_APPROVAL,
        "disable_security_controls": AutonomyLevel.HUMAN_APPROVAL,
        "change_billing": AutonomyLevel.HUMAN_APPROVAL,
        "deploy_breaking_change": AutonomyLevel.HUMAN_APPROVAL,
        
        # Forbidden
        "drop_database": AutonomyLevel.FORBIDDEN,
        "delete_client_data": AutonomyLevel.FORBIDDEN,
        "reset_database": AutonomyLevel.FORBIDDEN,
    }
    
    def get_level(self, action_name: str) -> AutonomyLevel:
        action = action_name.lower()
        for keyword, level in self.AUTONOMY_MAP.items():
            if keyword in action:
                return level
        return AutonomyLevel.COUNCIL_APPROVAL  # Default
    
    def requires_human(self, action_name: str) -> bool:
        return self.get_level(action_name) in (
            AutonomyLevel.HUMAN_APPROVAL,
            AutonomyLevel.FORBIDDEN,
        )
    
    def is_forbidden(self, action_name: str) -> bool:
        return self.get_level(action_name) == AutonomyLevel.FORBIDDEN