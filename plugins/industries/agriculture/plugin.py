"""Agriculture / Farm Operations sector pack."""
from plugins.industries.base import IndustryPlugin


class AgriculturePlugin(IndustryPlugin):
    pack_id = "agriculture"
    pack_version = "1.1"
    display_name = "Agriculture & Farm Operations"
    capabilities = ("farm_management", "field_management", "crop_cycle_management", "farm_activity_tracking", "harvest_management", "exception_management", "agricultural_outcomes")
    resources = ("farms", "fields", "crop_cycles", "farm_activities", "harvests", "field_exceptions", "agricultural_outcomes")
    policies = ("agriculture.workspace_access", "agriculture.field_access", "agriculture.crop_cycle_authorization", "agriculture.external_action_authorization", "agriculture.exception_integrity", "agriculture.outcome_integrity")
    workflows = ("farm_lifecycle", "crop_cycle_lifecycle", "field_operations", "harvest_operations", "farm_service", "field_exception_management", "agricultural_outcome_loop")
    events = ("farm.created", "field.created", "crop_cycle.created", "crop_cycle.updated", "activity.recorded", "harvest.recorded", "external_action.requested", "field.exception_recorded", "agriculture.outcome.observed")
    external_actions = ("agriculture.dispatch_field_task", "agriculture.request_input_delivery", "agriculture.record_exception", "agriculture.record_outcome")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import AgricultureRepository
        from .commands import AgricultureCommands
        self.repo = AgricultureRepository(supabase_client)
        self.commands_handler = AgricultureCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/agriculture/farm", "method": "POST", "handler": "handle_farm_webhook", "description": "Farm status/telemetry event"},
            {"path": "/webhooks/agriculture/crop-cycle", "method": "POST", "handler": "handle_crop_cycle_webhook", "description": "Crop cycle event"},
            {"path": "/webhooks/agriculture/harvest", "method": "POST", "handler": "handle_harvest_webhook", "description": "Harvest event"},
        ]
