from services.advanced_agent_control_plane import AdvancedAgentControlPlane, ControlPlaneState
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry
from services.v71_4_agent_planning import AgentPlanningRegistry, PlanStep


def _stack():
    identity = AgentIdentityRegistry("ws-1")
    identity.register(agent_id="a1", name="Ops", purpose="operate", declared_capabilities=("observe",))
    identity.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    permissions = AgentCapabilityPermissionRegistry("ws-1", identity)
    permissions.grant(permission_id="p1", agent_id="a1", capability="observe")
    planning = AgentPlanningRegistry("ws-1", identity, permissions)
    planning.create_plan(plan_id="plan1", agent_id="a1", objective="inspect", steps=(
        PlanStep("s1", "Observe", "observe", "inspect state"),
    ))
    return identity, permissions, planning


def test_snapshot_converges_existing_planes_without_authority():
    identity, permissions, planning = _stack()
    cp = AdvancedAgentControlPlane("ws-1", identity=identity, permissions=permissions, planning=planning)
    snap = cp.snapshot(agent_id="a1")
    assert snap.state == ControlPlaneState.READY
    assert snap.identity["agent_id"] == "a1"
    assert len(snap.permissions) == 1
    assert len(snap.plans) == 1
    assert snap.authority_granted is False
    assert snap.execution_requested is False
    assert snap.snapshot_fingerprint == snap.fingerprint()


def test_snapshot_is_deterministic_for_registry_order():
    identity, permissions, planning = _stack()
    cp = AdvancedAgentControlPlane("ws-1", identity=identity, permissions=permissions, planning=planning)
    a = cp.snapshot(agent_id="a1")
    b = cp.snapshot(agent_id="a1")
    assert a.canonical_payload() == b.canonical_payload()
    assert a.snapshot_fingerprint == b.snapshot_fingerprint


def test_inactive_agent_is_blocked():
    identity = AgentIdentityRegistry("ws-1")
    identity.register(agent_id="a1", name="Ops", purpose="operate")
    permissions = AgentCapabilityPermissionRegistry("ws-1", identity)
    cp = AdvancedAgentControlPlane("ws-1", identity=identity, permissions=permissions)
    snap = cp.snapshot(agent_id="a1")
    assert snap.state == ControlPlaneState.BLOCKED
    assert "agent_not_active" in snap.blocks


def test_workspace_isolation():
    identity = AgentIdentityRegistry("ws-1")
    identity.register(agent_id="a1", name="Ops", purpose="operate")
    permissions = AgentCapabilityPermissionRegistry("ws-1", identity)
    try:
        AdvancedAgentControlPlane("ws-2", identity=identity, permissions=permissions)
    except ValueError as exc:
        assert str(exc) == "workspace_mismatch"
    else:
        raise AssertionError("workspace mismatch must be rejected")


def test_context_remains_read_only():
    identity, permissions, planning = _stack()
    cp = AdvancedAgentControlPlane("ws-1", identity=identity, permissions=permissions, planning=planning)
    context = cp.context(cp.snapshot(agent_id="a1"))
    assert context["read_only"] is True
    assert context["authority"] == "none"
    assert context["authority_granted"] is False
    assert context["execution_requested"] is False
