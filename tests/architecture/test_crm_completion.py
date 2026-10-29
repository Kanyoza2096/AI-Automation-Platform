from plugins.industries.crm.plugin import CRMPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def test_crm_completion_manifest():
    manifest = CRMPlugin(None, None).get_manifest()
    assert manifest.version == "1.1"
    assert "activities" in manifest.resources
    assert "customer_outcomes" in manifest.resources
    assert "crm.outcome.observed" in manifest.events
    assert "crm.record_outcome" in manifest.external_actions


def test_customer_engagement_and_outcome_loop():
    p = CRMPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="tenant-a", role="owner"))
    try:
        contact = p.execute("add_contact", {"name": "Jane Doe", "phone": "0999000000"}, "tenant-a")["data"]
        deal = p.execute("add_deal", {"amount": 5000, "contact": "Jane Doe", "stage": "qualified"}, "tenant-a")["data"]
        activity = p.execute("record_activity", {"contact_id": contact["contact_id"], "deal_id": deal["deal_id"], "activity_type": "call", "subject": "Discovery", "notes": "Needs proposal"}, "tenant-a")
        assert activity["status"] == "recorded"
        outcome = p.execute("record_outcome", {"contact_id": contact["contact_id"], "deal_id": deal["deal_id"], "metric": "qualified_pipeline", "value": 5000, "source": "sales", "evidence": {"stage": "qualified"}}, "tenant-a")
        assert outcome["status"] == "observed"
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="tenant-b", role="owner"))
        assert p.execute("record_outcome", {"deal_id": deal["deal_id"], "metric": "x", "value": 1}, "tenant-b")["ok"] is True
        # The operation itself is tenant-bound; it cannot reference tenant-a data by lookup.
        assert p.execute("get_contacts", {"status": "lead"}, "tenant-b")["data"]["contacts"] == []
    finally:
        clear_request_context()


def test_crm_completion_commands_are_governed():
    commands = {x["intent"]: x for x in CRMPlugin(None, None).get_commands()}
    for name in ("record_activity", "record_outcome"):
        assert commands[name]["execution_mode"] == "action_kernel"
        assert commands[name]["governed_action_type"]
