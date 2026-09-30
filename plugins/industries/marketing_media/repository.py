"""Durable boundary for completed Marketing & Media operations."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from core.tenant_repository import require_workspace_id
from .models import Campaign, ContentSchedule, MarketingOutcome

logger = logging.getLogger("kanyoza.plugins.marketing_media")


class MarketingMediaRepository:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self._campaigns: dict[str, Campaign] = {}
        self._schedules: dict[str, ContentSchedule] = {}
        self._outcomes: dict[str, MarketingOutcome] = {}

    @staticmethod
    def _production() -> bool:
        try:
            from config.settings import settings
            import os
            mode = os.getenv("SERVICE_MODE", "").strip().lower()
            return bool(getattr(settings, "is_production", False)) and mode in {"full", "api", "core", "director", "meta", "condition", "workers", "monitor", "github", "chaos"}
        except Exception:
            return False

    def _require_durable_write(self, operation: str) -> None:
        if self.supabase is None and self._production():
            raise RuntimeError(f"durable marketing database is required for {operation}")

    def create_campaign(self, workspace_id: str, data: dict) -> Campaign:
        workspace_id = require_workspace_id(workspace_id, op="create_campaign")
        self._require_durable_write("create_campaign")
        campaign = Campaign(id=f"mkt-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
                            name=str(data.get("name", "")).strip(),
                            objective_id=str(data.get("objective_id", "")).strip(),
                            status=str(data.get("status", "draft")).lower(),
                            budget=float(data.get("budget", 0)))
        if not campaign.name or not campaign.objective_id:
            raise ValueError("campaign name and objective_id are required")
        if campaign.status not in {"draft", "active", "paused", "completed", "cancelled"}:
            raise ValueError("unsupported campaign status")
        if self.supabase:
            try:
                self.supabase.table("marketing_campaigns").insert(campaign.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Marketing campaign insert failed: %s", exc)
                raise RuntimeError("marketing campaign persistence failed") from exc
        self._campaigns[campaign.id] = campaign
        return campaign

    def get_campaign(self, workspace_id: str, campaign_id: str) -> Optional[Campaign]:
        workspace_id = require_workspace_id(workspace_id, op="get_campaign")
        if self.supabase:
            try:
                result = self.supabase.table("marketing_campaigns").select("*").eq("workspace_id", workspace_id).eq("id", campaign_id).limit(1).execute()
                if result.data:
                    return Campaign(**result.data[0])
            except Exception as exc:
                logger.warning("Marketing campaign lookup failed: %s", exc)
        value = self._campaigns.get(campaign_id)
        return value if value and value.workspace_id == workspace_id else None

    def update_campaign_status(self, workspace_id: str, campaign_id: str, status: str) -> Optional[Campaign]:
        workspace_id = require_workspace_id(workspace_id, op="update_campaign_status")
        self._require_durable_write("update_campaign_status")
        allowed = {"draft", "active", "paused", "completed", "cancelled"}
        if status not in allowed:
            raise ValueError("unsupported campaign status")
        now = datetime.now(timezone.utc)
        if self.supabase:
            try:
                result = self.supabase.table("marketing_campaigns").update({"status": status, "updated_at": now.isoformat()}).eq("workspace_id", workspace_id).eq("id", campaign_id).select("*").limit(1).execute()
                if result.data:
                    return Campaign(**result.data[0])
            except Exception as exc:
                logger.error("Marketing campaign update failed: %s", exc)
                raise RuntimeError("marketing campaign persistence failed") from exc
        campaign = self._campaigns.get(campaign_id)
        if campaign and campaign.workspace_id == workspace_id:
            updated = campaign.model_copy(update={"status": status, "updated_at": now})
            self._campaigns[campaign_id] = updated
            return updated
        return None

    def schedule_content(self, workspace_id: str, data: dict) -> ContentSchedule:
        workspace_id = require_workspace_id(workspace_id, op="schedule_content")
        self._require_durable_write("schedule_content")
        schedule = ContentSchedule(id=f"sch-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
                                   campaign_id=str(data.get("campaign_id", "")),
                                   candidate_id=str(data.get("candidate_id", "")),
                                   platform=str(data.get("platform", "")).strip().lower(),
                                   scheduled_at=data["scheduled_at"],
                                   decision_id=str(data.get("decision_id", "")).strip())
        if not all((schedule.campaign_id, schedule.candidate_id, schedule.platform, schedule.decision_id)):
            raise ValueError("campaign_id, candidate_id, platform and decision_id are required")
        if self.get_campaign(workspace_id, schedule.campaign_id) is None:
            raise ValueError("campaign not found")
        if self.supabase:
            try:
                self.supabase.table("marketing_content_schedules").insert(schedule.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Marketing schedule insert failed: %s", exc)
                raise RuntimeError("marketing schedule persistence failed") from exc
        self._schedules[schedule.id] = schedule
        return schedule

    def record_outcome(self, workspace_id: str, data: dict) -> MarketingOutcome:
        workspace_id = require_workspace_id(workspace_id, op="record_marketing_outcome")
        self._require_durable_write("record_marketing_outcome")
        outcome = MarketingOutcome(id=f"out-{uuid.uuid4().hex[:12]}", workspace_id=workspace_id,
                                   campaign_id=str(data.get("campaign_id", "")),
                                   candidate_id=data.get("candidate_id"),
                                   metric=str(data.get("metric", "")).strip(),
                                   value=float(data.get("value", 0)),
                                   source=str(data.get("source", "analytics")).strip(),
                                   evidence=data.get("evidence") or {})
        if not outcome.campaign_id or not outcome.metric:
            raise ValueError("campaign_id and metric are required")
        if self.get_campaign(workspace_id, outcome.campaign_id) is None:
            raise ValueError("campaign not found")
        if self.supabase:
            try:
                self.supabase.table("marketing_outcomes").insert(outcome.model_dump(mode="json")).execute()
            except Exception as exc:
                logger.error("Marketing outcome insert failed: %s", exc)
                raise RuntimeError("marketing outcome persistence failed") from exc
        self._outcomes[outcome.id] = outcome
        return outcome
