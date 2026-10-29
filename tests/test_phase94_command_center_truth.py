from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def test_command_center_critical_source_uses_canonical_system_health_key():
    text = (ROOT / "gateway" / "src" / "routes" / "commandCenter.js").read_text()
    assert "['systemHealth', '/api/v1/system/health']" in text
    assert "const critical = ['systemHealth', 'deepHealth', 'status', 'organismVitals'];" in text
    assert "const critical = ['system', 'deepHealth', 'status', 'organismVitals'];" not in text


def test_event_summary_builder_is_deterministic_and_non_invented():
    from core.event_api import _build_event_summary
    assert _build_event_summary("engine_alert", {"summary": "CPU elevated"}) == "CPU elevated"
    assert _build_event_summary("engine_alert", {"status": "critical"}) == "engine_alert reported status=critical."
    assert _build_event_summary("engine_alert", {}) == "engine_alert event observed; no producer summary supplied."


def test_event_api_wrapper_source_contains_summary_field():
    text = (ROOT / "kdo19" / "core" / "event_api.py").read_text()
    assert '"summary": _build_event_summary' in text
