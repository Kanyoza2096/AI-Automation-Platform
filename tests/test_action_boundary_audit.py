from pathlib import Path

from services.action_boundary_audit import audit


ROOT = Path(__file__).resolve().parents[1]


def test_consequential_plugin_execution_is_not_hidden_in_business_services():
    findings = audit(ROOT)
    plugin_findings = [f for f in findings if f.kind == "plugin_execute"]
    assert not plugin_findings, "direct plugin execution bypasses the Action Kernel: " + repr(plugin_findings)


def test_known_legacy_whatsapp_transport_is_explicitly_isolated():
    findings = audit(ROOT)
    legacy = [f for f in findings if f.file == "services/whatsApp.py"]
    assert not any(f.kind == "plugin_execute" for f in legacy)
