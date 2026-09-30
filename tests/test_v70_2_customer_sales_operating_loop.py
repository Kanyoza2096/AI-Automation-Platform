import asyncio
import pytest

from services.business_automation import BusinessAutomationEngine
from services.business_autonomy_envelope import BusinessAutonomyEnvelope, EnvelopeState, AutonomyMode, SuccessMetric, RiskBoundary
from services.v70_business_operating_layer import BusinessOperatingLayer, BusinessOperatingProfile, BusinessObjective, OperatingState, ObjectiveState
from services.v70_2_customer_sales_operating_loop import CustomerSalesOperatingLoop, LeadState


def make():
    engine = BusinessAutomationEngine()
    layer = BusinessOperatingLayer("ws1", engine=engine)
    layer.configure(BusinessOperatingProfile("ws1", "Acme", state=OperatingState.READY))
    layer.activate()
    env = BusinessAutonomyEnvelope(
        workspace_id="ws1", objective="Run sales loop",
        success_metrics=(SuccessMetric("sales_progress", ">=", 1),),
        allowed_actions=("crm_add_contact", "crm_update_contact_status", "crm_schedule_followup", "crm_update_deal_stage", "notify_operator"),
        scope={"workflow_ids": ("sales.new-lead-capture", "sales.qualified-lead-status", "sales.follow-up-due", "sales.deal-won", "sales.deal-lost", "sales.customer-opt-out")},
        risk_boundary=RiskBoundary(max_risk="medium"), mode=AutonomyMode.EXECUTE, state=EnvelopeState.ACTIVE,
    )
    obj = BusinessObjective("ws1", "Run sales loop", env, tuple(env.scope["workflow_ids"]), state=ObjectiveState.ACTIVE)
    layer.register_objective(obj)
    return CustomerSalesOperatingLoop("ws1", operating_layer=layer), obj


def test_lead_lifecycle_is_stateful_and_tenant_bound():
    loop, obj = make()
    r = asyncio.run(loop.process(event={"event_id":"e1","event_name":"lead.created","lead_id":"l1","name":"Jane"}, objective_id=obj.objective_id))
    assert r.accepted and r.state == LeadState.NEW and loop.snapshot("l1").version == 1
    r2 = asyncio.run(loop.process(event={"event_id":"e2","event_name":"lead.qualified","lead_id":"l1","contact_id":"l1"}, objective_id=obj.objective_id))
    assert r2.state == LeadState.QUALIFIED and r2.previous_state == LeadState.NEW


def test_invalid_transition_fails_closed():
    loop, obj = make()
    with pytest.raises(ValueError, match="invalid_transition"):
        asyncio.run(loop.process(event={"event_id":"e1","event_name":"lead.follow_up_due","lead_id":"l1"}, objective_id=obj.objective_id))


def test_duplicate_event_is_rejected():
    loop, obj = make()
    ev={"event_id":"e1","event_name":"lead.created","lead_id":"l1"}
    asyncio.run(loop.process(event=ev, objective_id=obj.objective_id))
    with pytest.raises(ValueError, match="duplicate_event"):
        asyncio.run(loop.process(event=ev, objective_id=obj.objective_id))


def test_cross_tenant_event_is_rejected():
    loop, obj = make()
    with pytest.raises(ValueError, match="tenant_mismatch"):
        asyncio.run(loop.process(event={"event_id":"e1","event_name":"lead.created","lead_id":"l1","workspace_id":"other"}, objective_id=obj.objective_id))


def test_opt_out_is_terminal():
    loop, obj = make()
    asyncio.run(loop.process(event={"event_id":"e1","event_name":"lead.created","lead_id":"l1"}, objective_id=obj.objective_id))
    r=asyncio.run(loop.process(event={"event_id":"e2","event_name":"customer.opted_out","lead_id":"l1"}, objective_id=obj.objective_id))
    assert r.state == LeadState.OPTED_OUT
    with pytest.raises(ValueError, match="opted_out"):
        asyncio.run(loop.process(event={"event_id":"e3","event_name":"lead.qualified","lead_id":"l1"}, objective_id=obj.objective_id))


def test_operating_layer_remains_the_only_execution_surface():
    loop, _ = make()
    assert loop.operating_layer.workspace_id == "ws1"
    assert not hasattr(loop, "action_kernel")
    assert not hasattr(loop, "connector")
    assert not hasattr(loop, "provider")
