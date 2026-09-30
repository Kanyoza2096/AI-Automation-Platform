"""Government / Public Services sector pack."""
from plugins.industries.base import IndustryPlugin


class GovernmentPlugin(IndustryPlugin):
    pack_id = "government"
    pack_version = "1.1"
    display_name = "Government & Public Services"
    capabilities = ("public_service_management", "service_requests", "permit_case_management", "case_management", "field_service", "resolution_management", "public_service_outcomes")
    resources = ("public_services", "service_requests", "permit_cases", "public_cases", "service_territories", "service_resolutions", "public_service_outcomes")
    policies = ("government.workspace_access", "government.service_access", "government.case_authorization", "government.external_action_authorization", "government.resolution_integrity", "government.outcome_integrity")
    workflows = ("service_request_lifecycle", "permit_case_lifecycle", "public_case_management", "field_service", "resolution_management", "public_service_outcomes")
    events = ("service.created", "service_request.created", "service_request.updated", "permit_case.created", "case.created", "external_action.requested", "service.resolution_recorded", "public_service.outcome.observed")
    external_actions = ("government.notify_applicant", "government.dispatch_field_service", "government.record_resolution", "government.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import GovernmentRepository
        from .commands import GovernmentCommands
        self.repo = GovernmentRepository(supabase_client)
        self.commands_handler = GovernmentCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/government/service-request", "method": "POST", "handler": "handle_service_request_webhook", "description": "Public service request event"},
            {"path": "/webhooks/government/permit", "method": "POST", "handler": "handle_permit_webhook", "description": "Permit case event"},
            {"path": "/webhooks/government/case", "method": "POST", "handler": "handle_case_webhook", "description": "Public case event"},
        ]
