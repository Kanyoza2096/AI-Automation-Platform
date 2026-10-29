"""Marketing & Media sector completion pack."""
from plugins.industries.base import IndustryPlugin


class MarketingMediaPlugin(IndustryPlugin):
    pack_id = "marketing_media"
    pack_version = "1.1"
    display_name = "Marketing & Media"
    capabilities = ("objectives", "campaigns", "content", "calendar", "publishing", "outcome_measurement")
    resources = ("marketing_objectives", "campaigns", "content_briefs", "content_candidates", "content_schedules", "marketing_outcomes")
    policies = ("marketing.workspace_access", "marketing.publish_authorization", "marketing.campaign_authorization", "marketing.outcome_integrity")
    workflows = ("campaign_lifecycle", "content_operating_loop", "governed_publishing", "campaign_outcome_loop")
    events = ("campaign.created", "campaign.updated", "content.scheduled", "marketing.outcome.observed")
    external_actions = ("publish_content", "marketing_schedule_content")

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .repository import MarketingMediaRepository
        from .commands import MarketingMediaCommands
        self.repo = MarketingMediaRepository(supabase_client)
        self.commands_handler = MarketingMediaCommands(self.repo, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        workspace_id = self.resolve_workspace(workspace_id)
        safe_params = {k: v for k, v in (params or {}).items() if k != "workspace_id"}
        return self.commands_handler.handle(command, safe_params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
            {"path": "/webhooks/marketing/campaign", "method": "POST", "handler": "handle_campaign_webhook", "description": "Campaign lifecycle event"},
            {"path": "/webhooks/marketing/outcome", "method": "POST", "handler": "handle_marketing_outcome_webhook", "description": "Observed campaign outcome from an analytics provider"},
        ]
