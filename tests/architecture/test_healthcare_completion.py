from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.healthcare.plugin import HealthcarePlugin


def test_healthcare_completion_manifest():
    m = HealthcarePlugin(None, None).get_manifest()
    assert m.version == "1.1"
    assert "care_plans" in m.resources
    assert "healthcare_outcomes" in m.resources
    assert "care_coordination" in m.capabilities
    assert "healthcare.outcome_integrity" in m.policies


def test_healthcare_completion_operating_loop_and_tenant_isolation():
    plugin = HealthcarePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="clinic-a", role="owner"))
    try:
        patient = plugin.execute("add_patient", {"full_name": "Patient A"}, "clinic-a")
        pid = patient["data"]["patient_id"]
        plan = plugin.execute("record_care_plan", {"patient_id": pid, "plan_type": "follow_up"}, "clinic-a")
        plan_id = plan["data"]["id"]
        updated = plugin.execute("update_care_plan_status", {"plan_id": plan_id, "status": "active"}, "clinic-a")
        assert updated["ok"] is True
        outcome = plugin.execute("record_outcome", {"patient_id": pid, "metric": "follow_up_completed", "value": 1, "evidence": {"source_id": "visit-1"}}, "clinic-a")
        assert outcome["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="clinic-b", role="owner"))
        assert plugin.execute("update_care_plan_status", {"plan_id": plan_id, "status": "completed"}, "clinic-b")["ok"] is False
    finally:
        clear_request_context()
