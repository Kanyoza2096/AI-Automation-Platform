from core.request_context import RequestContext, clear_request_context, set_request_context
from plugins.industries.education.plugin import EducationPlugin


def test_education_manifest_is_formal():
    m = EducationPlugin(None, None).get_manifest()
    assert m.pack_id == "education"
    assert m.version == "1.1"
    assert "students" in m.resources
    assert "education.send_parent_message" in m.external_actions


def test_education_commands_declare_execution_semantics():
    commands = {x["intent"]: x for x in EducationPlugin(None, None).get_commands()}
    assert commands["get_student"]["execution_mode"] == "read_only"
    assert commands["add_student"]["execution_mode"] == "action_kernel"
    assert commands["send_parent_message"]["governed_action_type"] == "education_send_parent_message"


def test_education_workspace_is_authoritative():
    plugin = EducationPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="school-a", role="owner"))
    try:
        result = plugin.execute("add_student", {"workspace_id": "school-b", "full_name": "Ada Student", "grade": "10"}, "school-b")
        assert result["workspace_id"] == "school-a"
    finally:
        clear_request_context()


def test_education_domain_flow_and_tenant_isolation():
    plugin = EducationPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="school-a", role="owner"))
    try:
        created = plugin.execute("add_student", {"full_name": "Ada Student", "grade": "10", "guardian_phone": "0888"}, "school-a")
        sid = created["data"]["student_id"]
        assert plugin.execute("get_student", {"student_id": sid}, "school-a")["ok"] is True
        assert plugin.execute("record_attendance", {"student_id": sid, "date": "2026-09-30"}, "school-a")["ok"] is True
        assert plugin.execute("record_assessment", {"student_id": sid, "subject": "Math", "score": 88}, "school-a")["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="school-b", role="owner"))
        assert plugin.execute("get_student", {"student_id": sid}, "school-b")["ok"] is False
    finally:
        clear_request_context()


def test_parent_messaging_is_connector_deferred():
    plugin = EducationPlugin(None, None)
    set_request_context(RequestContext(principal_id="u-1", workspace_id="school-a", role="owner"))
    try:
        result = plugin.execute("send_parent_message", {"student_id": "stu-1", "message": "Meeting tomorrow"}, "school-a")
        assert result["status"] == "action_required"
        assert result["execution_mode"] == "action_kernel"
        assert "connector layer" in result["message"]
    finally:
        clear_request_context()
