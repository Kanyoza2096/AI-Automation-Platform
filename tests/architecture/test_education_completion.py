from plugins.industries.education.plugin import EducationPlugin
from core.request_context import RequestContext, clear_request_context, set_request_context


def _plugin():
    return EducationPlugin(None, None)


def test_education_completion_manifest():
    m = _plugin().get_manifest()
    assert m.version == "1.1"
    assert "interventions" in m.resources
    assert "education_outcomes" in m.resources
    assert "education.outcome.observed" in m.events
    assert "education.record_outcome" in m.external_actions


def test_student_learning_and_outcome_loop():
    p = _plugin()
    set_request_context(RequestContext(principal_id="u-1", workspace_id="school-a", role="owner"))
    try:
        student = p.execute("add_student", {"full_name": "Ada Student", "grade": "10"}, "school-a")["data"]
        sid = student["student_id"]
        assert p.execute("record_attendance", {"student_id": sid, "date": "2026-09-30", "status": "absent"}, "school-a")["ok"] is True
        assert p.execute("record_assessment", {"student_id": sid, "subject": "Math", "score": 42}, "school-a")["ok"] is True
        intervention = p.execute("record_intervention", {"student_id": sid, "intervention_type": "academic_support", "reason": "Low assessment score"}, "school-a")
        assert intervention["status"] == "recorded"
        outcome = p.execute("record_outcome", {"student_id": sid, "metric": "attendance_rate", "value": 92, "source": "term_report", "evidence": {"term": "T1"}}, "school-a")
        assert outcome["status"] == "observed"
        status = p.execute("update_student_status", {"student_id": sid, "status": "active"}, "school-a")
        assert status["ok"] is True
        clear_request_context()
        set_request_context(RequestContext(principal_id="u-2", workspace_id="school-b", role="owner"))
        assert p.execute("get_student", {"student_id": sid}, "school-b")["ok"] is False
    finally:
        clear_request_context()


def test_completion_commands_are_governed():
    commands = {x["intent"]: x for x in _plugin().get_commands()}
    for name in ("update_student_status", "record_intervention", "record_outcome"):
        assert commands[name]["execution_mode"] == "action_kernel"
        assert commands[name]["governed_action_type"]
