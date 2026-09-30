from plugins.industries.manufacturing.plugin import ManufacturingPlugin
from plugins.industries.manufacturing.repository import ManufacturingRepository


def test_manifest_is_completion_version_and_declares_outcomes():
    p = ManufacturingPlugin()
    assert p.pack_version == "1.1"
    assert "material_consumption" in p.capabilities
    assert "production_outcomes" in p.capabilities
    assert "manufacturing_outcomes" in p.resources
    assert "production_outcome_loop" in p.workflows


def test_material_consumption_and_outcome_are_tenant_scoped():
    r = ManufacturingRepository()
    wc = r.create_work_center("ws-a", {"name": "Line A"})
    po = r.create_production_order("ws-a", {"reference": "PO-1", "product_sku": "SKU-1", "quantity": 100, "work_center_id": wc.id})
    material = r.record_material_consumption("ws-a", {"production_order_id": po.id, "material_sku": "MAT-1", "quantity": 25, "unit": "kg", "evidence_ref": "batch-1"})
    outcome = r.record_outcome("ws-a", {"production_order_id": po.id, "metric": "yield", "value": 94.0, "planned_quantity": 100, "produced_quantity": 94, "scrap_quantity": 6, "evidence_ref": "qc-1"})
    assert material.workspace_id == "ws-a"
    assert outcome.workspace_id == "ws-a"
    assert r.get_material_consumption("ws-b", material.id) is None
    assert r.get_outcome("ws-b", outcome.id) is None


def test_commands_are_governed():
    commands = ManufacturingPlugin().get_commands()
    by_intent = {c["intent"]: c for c in commands}
    assert by_intent["record_material_consumption"]["execution_mode"] == "action_kernel"
    assert by_intent["record_production_outcome"]["execution_mode"] == "action_kernel"
    assert by_intent["release_production"]["execution_mode"] == "action_kernel"
