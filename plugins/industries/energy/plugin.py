"""Energy & Utilities sector pack."""
from plugins.industries.base import IndustryPlugin


class EnergyPlugin(IndustryPlugin):
    pack_id = "energy"
    pack_version = "1.1"
    display_name = "Energy & Utilities"
    capabilities = ("asset_management", "metering", "incident_management", "work_order_management", "utility_operations", "exception_management", "utility_outcomes")
    resources = ("assets", "meter_readings", "incidents", "work_orders", "service_territories", "utility_exceptions", "utility_outcomes")
    policies = ("energy.workspace_access", "energy.asset_access", "energy.incident_authorization", "energy.remote_command_authorization", "energy.exception_integrity", "energy.outcome_integrity")
    workflows = ("asset_lifecycle", "incident_response", "metering_operations", "field_service", "utility_command", "utility_exception_management", "utility_outcome_loop")
    events = ("asset.created", "meter.recorded", "incident.created", "incident.updated", "work_order.created", "remote_command.requested", "utility.exception_recorded", "utility.outcome.observed")
    external_actions = ("energy.dispatch_crew", "energy.issue_remote_command", "energy.record_exception", "energy.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import EnergyRepository
        from .commands import EnergyCommands
        self.repo = EnergyRepository(supabase_client)
        self.commands_handler = EnergyCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/energy/asset", "method": "POST", "handler": "handle_asset_webhook", "description": "Utility asset telemetry/status event"},
            {"path": "/webhooks/energy/meter", "method": "POST", "handler": "handle_meter_webhook", "description": "Meter reading event"},
            {"path": "/webhooks/energy/incident", "method": "POST", "handler": "handle_incident_webhook", "description": "Utility incident event"},
        ]
