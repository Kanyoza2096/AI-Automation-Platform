import asyncio
import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep
from services.v71_9_bounded_autonomous_operations import BoundedAutonomousOperationsRegistry, AutonomousRunStatus
from services.v71_10_durable_workforce_runtime import DurableAutonomousWorkforceRuntime, DurableRuntimeBlock


def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="worker", purpose="business", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a1", capability="crm.read", scope={"workspace_id":"ws-1"})
    plans = AgentPlanningRegistry("ws-1", ids, perms)
    plans.create_plan(plan_id="p1", agent_id="a1", objective="inspect", steps=(
        PlanStep("s1", "read", "crm.read", "inspect", requested_scope={"workspace_id":"ws-1"}),))
    plans.assess("p1")
    ops = BoundedAutonomousOperationsRegistry("ws-1", ids, perms, plans)
    run = ops.create(run_id="r1", agent_id="a1", plan_id="p1", objective="inspect",
                     workflow_id="sales.qualified-lead-status", trigger="lead.qualified", event={"event_id":"e1"})
    run = ops._replace(run, status=AutonomousRunStatus.READY)
    return ops, run


def test_durable_runtime_claims_and_fences_in_memory():
    ops, run = setup()
    rt = DurableAutonomousWorkforceRuntime("ws-1", ops, allow_in_memory=True, worker_id="w1")
    snap = asyncio.run(rt.persist(run))
    assert snap.run.run_id == "r1"
    claim = asyncio.run(rt.claim("r1"))
    assert claim.lease.worker_id == "w1"
    assert claim.run.status == AutonomousRunStatus.RUNNING
    # Local execution state is deliberately not mutated by claim; the DB-backed
    # implementation mutates the authoritative row atomically.
    assert claim.authoritative is False


def test_expired_or_lost_ownership_cannot_finalize_as_success():
    ops, run = setup()
    rt = DurableAutonomousWorkforceRuntime("ws-1", ops, allow_in_memory=True, worker_id="w1")
    asyncio.run(rt.persist(run))
    lease = asyncio.run(rt.claim("r1")).lease
    other = DurableAutonomousWorkforceRuntime("ws-1", ops, allow_in_memory=True, worker_id="w2")
    with pytest.raises(ValueError, match=DurableRuntimeBlock.LEASE_NOT_OWNED.value):
        asyncio.run(other.finalize(lease, status=AutonomousRunStatus.COMPLETED))


def test_stale_running_run_is_paused_for_reconciliation_not_retried():
    ops, run = setup()
    # Simulate the authoritative state reached RUNNING after a worker claimed it.
    running = ops._replace(run, status=AutonomousRunStatus.RUNNING)
    rt = DurableAutonomousWorkforceRuntime("ws-1", ops, allow_in_memory=True, worker_id="w1")
    snap = asyncio.run(rt.mark_stale_for_reconciliation("r1"))
    assert snap.run.status == AutonomousRunStatus.PAUSED
    assert snap.reconciliation_required is True
    assert snap.run.result_summary["reconciliation_required"] is True


def test_production_runtime_fails_closed_without_store():
    ops, run = setup()
    rt = DurableAutonomousWorkforceRuntime("ws-1", ops, allow_in_memory=False)
    with pytest.raises(RuntimeError, match=DurableRuntimeBlock.DURABLE_STORE_REQUIRED.value):
        asyncio.run(rt.persist(run))
