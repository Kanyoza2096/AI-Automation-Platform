from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_command_center_critical_source_uses_canonical_system_health_key():
    text = (ROOT / "services" / "v72_2_command_center_read_model.py").read_text()
    assert '"systemHealth"' in text
    assert "businessHealth" in text
    assert '"system"' not in text.split("businessHealth", 1)[1].split("\n", 8)[0]


def test_event_summary_builder_is_deterministic_and_non_invented():
    from core.event_api import _build_event_summary
    assert _build_event_summary("engine_alert", {"summary": "CPU elevated"}) == "CPU elevated"
    assert _build_event_summary("engine_alert", {"status": "critical"}) == "engine_alert reported status=critical."
    assert _build_event_summary("engine_alert", {}) == "engine_alert event observed; no producer summary supplied."


def test_event_api_wrapper_source_contains_summary_field():
    text = (ROOT / "core" / "event_api.py").read_text()
    assert '"summary": _build_event_summary' in text
