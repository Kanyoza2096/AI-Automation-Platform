from plugins.industries.energy.plugin import EnergyPlugin
from plugins.industries.energy.repository import EnergyRepository


def test_manifest_is_completion_version_and_declares_outcomes():
    p = EnergyPlugin()
    assert p.pack_version == "1.1"
    assert "exception_management" in p.capabilities
    assert "utility_outcomes" in p.capabilities
    assert "utility_outcomes" in p.resources
    assert "utility_outcome_loop" in p.workflows
    assert "energy.record_outcome" in p.external_actions


def test_exception_and_outcome_loop_is_tenant_scoped():
    r = EnergyRepository()
    asset = r.create_asset("ws-a", {"name": "Feeder A", "asset_type": "feeder"})
    incident = r.create_incident("ws-a", {"asset_id": asset.id, "title": "Voltage event"})
    exc = r.record_exception("ws-a", {"asset_id": asset.id, "exception_type": "voltage_variance", "severity": "high", "evidence_ref": "meter-1"})
    outcome = r.record_outcome("ws-a", {"reference_type": "incident", "reference_id": incident.id, "outcome_type": "restoration_time", "metric": "minutes", "value": 42, "evidence_ref": "dispatch-1"})
    assert exc.workspace_id == "ws-a"
    assert outcome.workspace_id == "ws-a"
    assert r.get_exception("ws-b", exc.id) is None
    assert r.get_outcome("ws-b", outcome.id) is None


def test_commands_are_governed():
    by_intent = {c["intent"]: c for c in EnergyPlugin().get_commands()}
    assert by_intent["record_exception"]["execution_mode"] == "action_kernel"
    assert by_intent["record_outcome"]["execution_mode"] == "action_kernel"
