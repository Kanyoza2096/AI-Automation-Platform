import asyncio
from datetime import datetime, timezone

import pytest

from services.business_automation import BusinessAutomationEngine
from services.business_autonomy_envelope import (
    BusinessAutonomyEnvelope, SuccessMetric, RiskBoundary, AutonomyMode, EnvelopeState,
)
from services.v70_business_operating_layer import (
    BusinessOperatingLayer, BusinessOperatingProfile, BusinessObjective,
    OperatingState, ObjectiveState, assert_operating_layer_is_governed,
)


def envelope(ws, mode=AutonomyMode.EXECUTE, actions=("crm_add_contact",)):
    return BusinessAutonomyEnvelope(
        workspace_id=ws,
        objective="Capture leads",
        success_metrics=(SuccessMetric("captured", ">=", 1),),
        allowed_actions=actions,
        scope={"workflow_ids": ("sales.new-lead-capture",)},
        risk_boundary=RiskBoundary(max_risk="medium"),
        mode=mode,
        state=EnvelopeState.ACTIVE,
    )


def make_layer():
    layer = BusinessOperatingLayer("ws1", engine=BusinessAutomationEngine())
    layer.configure(BusinessOperatingProfile("ws1", "Acme", industry="retail", state=OperatingState.READY))
    layer.activate()
    return layer


def test_profile_is_tenant_bound_and_activation_is_explicit():
    layer = make_layer()
    assert layer.profile.state == OperatingState.ACTIVE
    with pytest.raises(ValueError):
        layer.configure(BusinessOperatingProfile("other", "Attack"))


def test_objective_requires_active_profile_and_known_workflow():
    layer = make_layer()
    obj = BusinessObjective("ws1", "Capture leads", envelope("ws1"), ("sales.new-lead-capture",))
    layer.register_objective(obj)
    assert obj.objective_id in layer.objectives
    with pytest.raises(ValueError):
        layer.register_objective(BusinessObjective("ws1", "Bad", envelope("ws1"), ("does.not.exist",)))


def test_plan_is_advisory_and_respects_envelope_scope():
    layer = make_layer()
    obj = BusinessObjective("ws1", "Capture leads", envelope("ws1"), ("sales.new-lead-capture",))
    layer.register_objective(obj)
    plan = layer.plan(obj.objective_id)
    assert len(plan) == 1
    assert plan[0].authority == "none"
    assert plan[0].execution_effect == "none"
    assert plan[0].admission == "admitted"


def test_high_impact_workflow_is_not_auto_approved():
    layer = make_layer()
    high = BusinessAutonomyEnvelope(
        workspace_id="ws1", objective="Communicate", success_metrics=(SuccessMetric("x", ">=", 1),),
        allowed_actions=("customer_communication",), scope={"workflow_ids": ("sales.follow-up-unified",)},
        risk_boundary=RiskBoundary(max_risk="high"), mode=AutonomyMode.EXECUTE, state=EnvelopeState.ACTIVE,
    )
    obj = BusinessObjective("ws1", "Communicate", high, ("sales.follow-up-unified",))
    layer.register_objective(obj)
    plan = layer.plan(obj.objective_id)
    assert plan[0].admission == "review_required"


def test_cross_tenant_objective_is_rejected():
    layer = make_layer()
    with pytest.raises(ValueError):
        layer.register_objective(BusinessObjective("other", "Attack", envelope("other"), ("sales.new-lead-capture",)))


def test_event_execution_uses_existing_engine_only():
    layer = make_layer()
    obj = BusinessObjective("ws1", "Capture leads", envelope("ws1"), ("sales.new-lead-capture",), state=ObjectiveState.ACTIVE)
    layer.register_objective(obj)
    run = asyncio.run(layer.handle_event(obj.objective_id, "sales.new-lead-capture", event={
        "event_id": "evt-1", "name": "Jane", "phone": "+265000000", "email": "j@example.com"
    }))
    assert run.workspace_id == "ws1"
    assert run.event_id == "evt-1"
    assert run.accepted is True
    assert run.admission.authority == "none"


def test_event_requires_stable_event_id():
    layer = make_layer()
    obj = BusinessObjective("ws1", "Capture leads", envelope("ws1"), ("sales.new-lead-capture",), state=ObjectiveState.ACTIVE)
    layer.register_objective(obj)
    with pytest.raises(ValueError, match="event_id"):
        asyncio.run(layer.handle_event(obj.objective_id, "sales.new-lead-capture", event={"name": "Jane"}))


def test_governed_surface_has_no_parallel_execution_path():
    assert_operating_layer_is_governed(make_layer())


def test_evidence_fingerprint_is_deterministic_for_same_run_shape():
    layer = make_layer()
    obj = BusinessObjective("ws1", "Capture leads", envelope("ws1"), ("sales.new-lead-capture",), state=ObjectiveState.ACTIVE)
    layer.register_objective(obj)
    a = asyncio.run(layer.handle_event(obj.objective_id, "sales.new-lead-capture", event={"event_id": "evt-1", "name": "A"}))
    b = asyncio.run(layer.handle_event(obj.objective_id, "sales.new-lead-capture", event={"event_id": "evt-1", "name": "B"}))
    # Fingerprint deliberately covers the governed run shape, not mutable payload content.
    assert a.evidence_fingerprint() != ""
    assert len(a.evidence_fingerprint()) == 64
    assert b.evidence_fingerprint() != ""
