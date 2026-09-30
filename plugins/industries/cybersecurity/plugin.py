"""Cybersecurity Operations sector completion pack."""
from plugins.industries.base import IndustryPlugin


class CybersecurityPlugin(IndustryPlugin):
    pack_id = "cybersecurity"
    pack_version = "1.1"
    display_name = "Cybersecurity Operations"
    capabilities = ("asset_inventory", "finding_management", "security_alerting", "incident_response", "response_outcomes", "security_outcomes")
    resources = ("assets", "findings", "alerts", "security_incidents", "security_response_outcomes", "security_outcomes", "security_policies")
    policies = ("security.workspace_access", "security.finding_access", "security.response_authorization", "security.containment_authorization", "security.outcome_integrity")
    workflows = ("finding_lifecycle", "alert_triage", "security_incident_response", "security_response_verification", "security_outcome_loop", "asset_containment")
    events = ("asset.created", "finding.created", "finding.updated", "alert.created", "security.incident.created", "security.incident.updated", "security.response.outcome.observed", "security.outcome.observed", "containment.requested", "remediation.requested")
    external_actions = ("security.contain_asset", "security.remediate_finding", "security.record_response_outcome", "security.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import CybersecurityRepository
        from .commands import CybersecurityCommands
        self.repo = CybersecurityRepository(supabase_client)
        self.commands_handler = CybersecurityCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k:v for k,v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
            {"path":"/webhooks/security/alert","method":"POST","handler":"handle_security_alert_webhook","description":"Security alert event from a SIEM/XDR/monitoring provider"},
            {"path":"/webhooks/security/finding","method":"POST","handler":"handle_security_finding_webhook","description":"Security finding event from a vulnerability or security platform"},
            {"path":"/webhooks/security/response","method":"POST","handler":"handle_security_response_webhook","description":"Authoritative security response outcome from a connector"},
        ]
