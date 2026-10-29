import asyncio
import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep
from services.v71_9_bounded_autonomous_operations import BoundedAutonomousOperationsRegistry, AutonomousRunStatus
from services.v71_11_workforce_observability_control import WorkforceObservabilityControlPlane, ControlCommand, ControlBlock


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="worker", purpose="business", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a1", capability="crm.read", scope={"workspace_id":"ws-1"})
    plans = AgentPlanningRegistry("ws-1", ids, perms)
    plans.create_plan(plan_id="p1", agent_id="a1", objective="inspect", steps=(PlanStep("s1", "read", "crm.read", "inspect", requested_scope={"workspace_id":"ws-1"}),))
    plans.assess("p1")
    ops = BoundedAutonomousOperationsRegistry("ws-1", ids, perms, plans)
    run = ops.create(run_id="r1", agent_id="a1", plan_id="p1", objective="inspect", workflow_id="sales.qualified-lead-status", trigger="lead.qualified", event={"event_id":"e1"})
    run = ops._replace(run, status=AutonomousRunStatus.READY)
    return ops, run


def test_operator_can_pause_and_resume_only_when_not_uncertain():
    ops, _ = setup(); cp = WorkforceObservabilityControlPlane("ws-1", ops, allow_in_memory=True, operator_id="op1")
    paused = asyncio.run(cp.control("r1", ControlCommand.PAUSE, reason="maintenance"))
    assert paused.run.status == AutonomousRunStatus.PAUSED
    resumed = asyncio.run(cp.control("r1", ControlCommand.RESUME, reason="maintenance complete"))
    assert resumed.run.status == AutonomousRunStatus.READY


def test_running_pause_is_fenced_for_reconciliation_and_cannot_resume():
    ops, run = setup(); ops._replace(run, status=AutonomousRunStatus.RUNNING)
    cp = WorkforceObservabilityControlPlane("ws-1", ops, allow_in_memory=True)
    paused = asyncio.run(cp.control("r1", ControlCommand.PAUSE, reason="operator stop"))
    assert paused.reconciliation_required is True
    with pytest.raises(ValueError, match=ControlBlock.RESUME_REQUIRES_RECONCILIATION.value):
        asyncio.run(cp.control("r1", ControlCommand.RESUME))


def test_cancel_is_terminal_and_replay_is_blocked():
    ops, _ = setup(); cp = WorkforceObservabilityControlPlane("ws-1", ops, allow_in_memory=True)
    cancelled = asyncio.run(cp.control("r1", ControlCommand.CANCEL, reason="operator request"))
    assert cancelled.run.status == AutonomousRunStatus.CANCELLED
    with pytest.raises(ValueError, match=ControlBlock.TERMINAL.value):
        asyncio.run(cp.control("r1", ControlCommand.CANCEL))


def test_observability_never_grants_authority():
    ops, _ = setup(); cp = WorkforceObservabilityControlPlane("ws-1", ops, allow_in_memory=True)
    snap = asyncio.run(cp.snapshot("r1"))
    assert snap.health == "READY"
    assert not hasattr(snap, "authority_granted")
    assert "resume" not in snap.controls_available
