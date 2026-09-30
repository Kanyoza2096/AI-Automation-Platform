"""Command surface for completed Marketing & Media operations."""
from datetime import datetime
from .repository import MarketingMediaRepository


class MarketingMediaCommands:
    def __init__(self, repository: MarketingMediaRepository, redis_client=None):
        self.repo = repository
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        return [
            {"intent": "create_campaign", "description": "Create a governed marketing campaign", "params": ["name", "objective_id", "budget"], "category": "campaigns", "execution_mode": "action_kernel", "governed_action_type": "marketing_create_campaign"},
            {"intent": "get_campaign", "description": "Read a campaign", "params": ["campaign_id"], "category": "campaigns", "execution_mode": "read_only"},
            {"intent": "update_campaign_status", "description": "Change campaign lifecycle state", "params": ["campaign_id", "status"], "category": "campaigns", "execution_mode": "action_kernel", "governed_action_type": "marketing_update_campaign_status"},
            {"intent": "schedule_content", "description": "Schedule an already-governed content candidate", "params": ["campaign_id", "candidate_id", "platform", "scheduled_at", "decision_id"], "category": "calendar", "execution_mode": "action_kernel", "governed_action_type": "marketing_schedule_content"},
            {"intent": "record_outcome", "description": "Record an observed campaign outcome without granting execution authority", "params": ["campaign_id", "candidate_id", "metric", "value", "source", "evidence"], "category": "analytics", "execution_mode": "action_kernel", "governed_action_type": "marketing_record_outcome"},
        ]

    def handle(self, command: str, params: dict, workspace_id: str) -> dict:
        handler = {
            "create_campaign": self._create_campaign,
            "get_campaign": self._get_campaign,
            "update_campaign_status": self._update_campaign_status,
            "schedule_content": self._schedule_content,
            "record_outcome": self._record_outcome,
        }.get(command)
        if not handler:
            return {"ok": False, "error": f"Unknown marketing command: {command}"}
        return handler(params, workspace_id)

    def _create_campaign(self, params, workspace_id):
        campaign = self.repo.create_campaign(workspace_id, params)
        return {"ok": True, "data": campaign.model_dump(mode="json")}

    def _get_campaign(self, params, workspace_id):
        campaign = self.repo.get_campaign(workspace_id, str(params.get("campaign_id", "")))
        return {"ok": bool(campaign), "data": campaign.model_dump(mode="json") if campaign else None, "error": None if campaign else "Campaign not found"}

    def _update_campaign_status(self, params, workspace_id):
        campaign = self.repo.update_campaign_status(workspace_id, str(params.get("campaign_id", "")), str(params.get("status", "")).lower())
        return {"ok": bool(campaign), "data": campaign.model_dump(mode="json") if campaign else None, "error": None if campaign else "Campaign not found"}

    def _schedule_content(self, params, workspace_id):
        data = dict(params)
        value = data.get("scheduled_at")
        if isinstance(value, str):
            data["scheduled_at"] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        schedule = self.repo.schedule_content(workspace_id, data)
        return {"ok": True, "status": "scheduled", "execution_mode": "action_kernel", "data": schedule.model_dump(mode="json")}

    def _record_outcome(self, params, workspace_id):
        outcome = self.repo.record_outcome(workspace_id, params)
        return {"ok": True, "status": "observed", "execution_mode": "action_kernel", "data": outcome.model_dump(mode="json")}
