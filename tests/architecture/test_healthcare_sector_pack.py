from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.healthcare.plugin import HealthcarePlugin


def test_healthcare_manifest_is_formal():
    m = HealthcarePlugin(None, None).get_manifest()
    assert m.pack_id == "healthcare"
    assert m.version == "1.1"
    assert "care_coordination" in m.capabilities
    assert "healthcare.outcome_integrity" in m.policies
    assert "patients" in m.resources
    assert "healthcare.send_patient_message" in m.external_actions
    assert "healthcare.patient_access" in m.policies


def test_healthcare_commands_declare_execution_semantics():
    commands = {x["intent"]: x for x in HealthcarePlugin(None, None).get_commands()}
    assert commands["get_patient"]["execution_mode"] == "read_only"
    assert commands["add_patient"]["execution_mode"] == "action_kernel"
    assert commands["send_patient_message"]["governed_action_type"] == "healthcare_send_patient_message"


def test_healthcare_workspace_is_authoritative():
    plugin = HealthcarePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="clinic-a", role="owner"))
    try:
        result = plugin.execute("add_patient", {"workspace_id": "clinic-b", "full_name": "Patient A"}, "clinic-b")
        assert result["workspace_id"] == "clinic-a"
    finally:
        clear_request_context()


def test_healthcare_domain_flow_and_tenant_isolation():
    plugin = HealthcarePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="clinic-a", role="owner"))
    try:
        created = plugin.execute("add_patient", {"full_name": "Patient A", "phone": "0888"}, "clinic-a")
        pid = created["data"]["patient_id"]
        appointment = plugin.execute("schedule_appointment", {"patient_id": pid, "scheduled_for": "2026-10-01T09:00:00", "department": "outpatient"}, "clinic-a")
        aid = appointment["data"]["appointment_id"]
        assert plugin.execute("update_appointment_status", {"appointment_id": aid, "status": "confirmed"}, "clinic-a")["ok"] is True
        assert plugin.execute("open_encounter", {"patient_id": pid, "appointment_id": aid}, "clinic-a")["ok"] is True
        assert plugin.execute("create_invoice", {"patient_id": pid, "amount": 25000}, "clinic-a")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="clinic-b", role="owner"))
        assert plugin.execute("get_patient", {"patient_id": pid}, "clinic-b")["ok"] is False
        assert plugin.execute("get_appointment", {"appointment_id": aid}, "clinic-b")["ok"] is False
    finally:
        clear_request_context()


def test_patient_messaging_is_connector_deferred():
    plugin = HealthcarePlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="clinic-a", role="owner"))
    try:
        result = plugin.execute("send_patient_message", {"patient_id": "pat-1", "message": "Your appointment is confirmed"}, "clinic-a")
        assert result["status"] == "action_required"
        assert result["execution_mode"] == "action_kernel"
        assert "connector layer" in result["message"]
    finally:
        clear_request_context()
