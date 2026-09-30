"""Manufacturing / Industrial Operations sector pack."""
from plugins.industries.base import IndustryPlugin


class ManufacturingPlugin(IndustryPlugin):
    pack_id = "manufacturing"
    pack_version = "1.1"
    display_name = "Manufacturing & Industrial Operations"
    capabilities = ("work_center_management", "production_orders", "quality_management", "maintenance_governance", "material_consumption", "production_outcomes")
    resources = ("work_centers", "production_orders", "quality_inspections", "maintenance_orders", "material_consumption", "manufacturing_outcomes", "production_lines")
    policies = ("manufacturing.workspace_access", "manufacturing.production_authorization", "manufacturing.quality_access", "manufacturing.maintenance_authorization")
    workflows = ("production_lifecycle", "quality_control", "maintenance_lifecycle", "production_release", "material_consumption", "production_outcome_loop")
    events = ("work_center.created", "production_order.created", "production.updated", "quality.recorded", "maintenance.requested", "material_consumption.recorded", "production_outcome.recorded")
    external_actions = ("manufacturing.release_production", "manufacturing.request_maintenance")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import ManufacturingRepository
        from .commands import ManufacturingCommands
        self.repo = ManufacturingRepository(supabase_client)
        self.commands_handler = ManufacturingCommands(self.repo, redis_client)

    def get_commands(self):
        return self.commands_handler.list_all()

    def execute(self, command, params, workspace_id="default"):
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self):
        return [
            {"path": "/webhooks/manufacturing/production", "method": "POST", "handler": "handle_production_webhook", "description": "Production-line status event"},
            {"path": "/webhooks/manufacturing/quality", "method": "POST", "handler": "handle_quality_webhook", "description": "Quality-control result event"},
            {"path": "/webhooks/manufacturing/maintenance", "method": "POST", "handler": "handle_maintenance_webhook", "description": "Maintenance system event"},
        ]
