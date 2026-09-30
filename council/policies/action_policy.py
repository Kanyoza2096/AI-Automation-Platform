"""Action classification policy."""

from enum import Enum
from typing import Dict, List


class ActionClass(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK = "high_risk"
    CRITICAL = "critical"
    IRREVERSIBLE = "irreversible"


class ActionPolicy:
    """Classifies actions by risk and required deliberation."""
    
    # Action classification map
    ACTION_CLASSES: Dict[str, ActionClass] = {
        # Read-only
        "get_status": ActionClass.READ_ONLY,
        "get_stats": ActionClass.READ_ONLY,
        "refresh_metrics": ActionClass.READ_ONLY,
        "update_cache": ActionClass.READ_ONLY,
        "log_event": ActionClass.READ_ONLY,
        "format_response": ActionClass.READ_ONLY,
        
        # Low risk
        "generate_draft": ActionClass.LOW_RISK,
        "analyze_performance": ActionClass.LOW_RISK,
        "schedule_content": ActionClass.LOW_RISK,
        "calculate_health": ActionClass.LOW_RISK,
        "select_strategy": ActionClass.LOW_RISK,
        
        # Medium risk
        "publish_content": ActionClass.MEDIUM_RISK,
        "boost_content": ActionClass.MEDIUM_RISK,
        "reduce_content": ActionClass.MEDIUM_RISK,
        "change_posting_time": ActionClass.MEDIUM_RISK,
        "run_experiment": ActionClass.MEDIUM_RISK,
        
        # High risk
        "restart_service": ActionClass.HIGH_RISK,
        "safe_mode": ActionClass.HIGH_RISK,
        "change_billing": ActionClass.HIGH_RISK,
        "deploy_breaking_change": ActionClass.HIGH_RISK,
        "send_legal_notice": ActionClass.HIGH_RISK,
        
        # Critical
        "rotate_credentials": ActionClass.CRITICAL,
        "change_production_secrets": ActionClass.CRITICAL,
        "disable_security_controls": ActionClass.CRITICAL,
        "change_access_control": ActionClass.CRITICAL,
        
        # Irreversible
        "drop_database": ActionClass.IRREVERSIBLE,
        "reset_database": ActionClass.IRREVERSIBLE,
        "delete_client_data": ActionClass.IRREVERSIBLE,
        "delete_brand": ActionClass.IRREVERSIBLE,
        "refund_client": ActionClass.IRREVERSIBLE,
        "delete_workspace": ActionClass.IRREVERSIBLE,
        "wipe_workspace": ActionClass.IRREVERSIBLE,
        "purge_data": ActionClass.IRREVERSIBLE,
        "destroy_storage": ActionClass.IRREVERSIBLE,
        "terminate_account": ActionClass.IRREVERSIBLE,
    }
    
    # Deliberation requirements per class
    DELIBERATION_REQUIREMENTS: Dict[ActionClass, Dict] = {
        ActionClass.READ_ONLY: {"tier": 0, "personas": 0, "human_approval": False},
        ActionClass.LOW_RISK: {"tier": 1, "personas": 2, "human_approval": False},
        ActionClass.MEDIUM_RISK: {"tier": 2, "personas": 5, "human_approval": False},
        ActionClass.HIGH_RISK: {"tier": 3, "personas": 10, "human_approval": False},
        ActionClass.CRITICAL: {"tier": 3, "personas": 10, "human_approval": True},
        ActionClass.IRREVERSIBLE: {"tier": 3, "personas": 10, "human_approval": True},
    }
    
    def classify(self, action_name: str) -> ActionClass:
        """Classify an action with a conservative destructive-action floor."""
        action = action_name.lower().strip()
        # Explicit irreversible/destructive language always wins over a
        # weaker substring match (for example ``delete_brand_cache``).
        destructive_markers = (
            "drop_database", "reset_database", "delete_client_data",
            "delete_workspace", "wipe_workspace", "purge_data",
            "destroy_storage", "terminate_account", "delete_brand",
        )
        if any(marker in action for marker in destructive_markers):
            return ActionClass.IRREVERSIBLE
        for keyword, action_class in self.ACTION_CLASSES.items():
            if keyword in action:
                return action_class
        return ActionClass.MEDIUM_RISK  # Conservative default
    
    def get_requirements(self, action_class: ActionClass) -> Dict:
        """Get deliberation requirements for an action class."""
        return self.DELIBERATION_REQUIREMENTS.get(
            action_class,
            {"tier": 2, "personas": 5, "human_approval": False},
        )