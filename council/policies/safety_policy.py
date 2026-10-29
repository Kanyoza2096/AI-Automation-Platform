"""Safety policy — absolutely forbidden actions."""

class SafetyPolicy:
    """Defines completely forbidden actions."""
    
    FORBIDDEN_ACTIONS = {
        "drop_database",
        "reset_database",
        "delete_client_data",
        "delete_brand",
        "disable_security_controls",
        "change_production_secrets",
        "send_legal_notice",
        "change_access_control",
    }
    
    FORBIDDEN_PATTERNS = {
        "delete all", "drop all", "truncate", "wipe",
        "destroy", "permanently delete", "remove all",
    }
    
    def is_forbidden(self, action_name: str, description: str = "") -> bool:
        """Check if action is completely forbidden."""
        action = action_name.lower()
        desc = description.lower()
        
        for forbidden in self.FORBIDDEN_ACTIONS:
            if forbidden in action:
                return True
        
        for pattern in self.FORBIDDEN_PATTERNS:
            if pattern in desc:
                return True
        
        return False
    
    def requires_human_approval(self, action_name: str) -> bool:
        """Check if action requires human approval."""
        action = action_name.lower()
        
        human_required = {
            "rotate_credentials", "change_billing", "refund",
            "deploy_breaking", "change_secrets",
        }
        
        for keyword in human_required:
            if keyword in action:
                return True
        
        return False