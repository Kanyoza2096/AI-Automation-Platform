from datetime import datetime, timezone
from plugins.industries.marketing_media.plugin import MarketingMediaPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def test_marketing_completion_manifest_is_explicit():
    manifest = MarketingMediaPlugin(None, None).get_manifest()
    assert manifest.pack_id == "marketing_media"
    assert manifest.version == "1.1"
    assert "campaigns" in manifest.resources
    assert "marketing.outcome.observed" in manifest.events
    assert "publish_content" in manifest.external_actions


def test_campaign_calendar_and_outcome_flow_is_workspace_bound():
    plugin = MarketingMediaPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        campaign = plugin.execute("create_campaign", {"name": "Q4 Demand", "objective_id": "obj-1", "budget": 1000}, "tenant-a")["data"]
        assert campaign["status"] == "draft"
        assert plugin.execute("update_campaign_status", {"campaign_id": campaign["id"], "status": "active"}, "tenant-a")["ok"]
        scheduled = plugin.execute("schedule_content", {"campaign_id": campaign["id"], "candidate_id": "cand-1", "platform": "linkedin", "scheduled_at": datetime.now(timezone.utc).isoformat(), "decision_id": "dec-1"}, "tenant-a")
        assert scheduled["status"] == "scheduled"
        outcome = plugin.execute("record_outcome", {"campaign_id": campaign["id"], "candidate_id": "cand-1", "metric": "qualified_leads", "value": 12, "source": "analytics", "evidence": {"window": "7d"}}, "tenant-a")
        assert outcome["status"] == "observed"
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="tenant-b", role="owner"))
        assert plugin.execute("get_campaign", {"campaign_id": campaign["id"]}, "tenant-b")["ok"] is False
    finally:
        clear_request_context()


def test_marketing_commands_declare_governed_boundaries():
    commands = {item["intent"]: item for item in MarketingMediaPlugin(None, None).get_commands()}
    assert commands["get_campaign"]["execution_mode"] == "read_only"
    assert commands["create_campaign"]["execution_mode"] == "action_kernel"
    assert commands["schedule_content"]["governed_action_type"] == "marketing_schedule_content"
