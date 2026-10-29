import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import AgentCapabilityPermissionRegistry, PermissionEffect
from services.v71_8_multi_agent_coordination import (
    MultiAgentCoordinationRegistry, CoordinationTask, CoordinationStatus,
    CoordinationDisposition, CoordinationBlock,
)


def setup():
    ids = AgentIdentityRegistry("ws-1")
    for aid in ("a1", "a2"):
        ids.register(agent_id=aid, name=aid, purpose="work", declared_capabilities=("crm.read", "notify"))
        ids.transition(agent_id=aid, status=AgentStatus.ACTIVE)
    perms = AgentCapabilityPermissionRegistry("ws-1", ids)
    perms.grant(permission_id="p1", agent_id="a2", capability="crm.read", scope={"workspace_id": "ws-1"})
    return ids, perms, MultiAgentCoordinationRegistry("ws-1", ids, perms)


def test_coordination_assesses_target_independently_and_never_executes():
    _, _, reg = setup()
    reg.create(coordination_id="c1", coordinator_agent_id="a1", target_agent_id="a2",
               objective="research", tasks=(CoordinationTask("t1", "Read CRM", "crm.read", {"workspace_id": "ws-1"}),))
    result = reg.assess("c1")
    assert result.status == CoordinationStatus.ACCEPTED
    assert result.dispositions["t1"] == CoordinationDisposition.ACCEPTED_FOR_GOVERNANCE
    assert result.authority_granted is False
    assert result.execution_requested is False
    assert reg.coordination_context("c1")["authority_granted"] is False


def test_missing_target_capability_is_not_invented():
    _, _, reg = setup()
    reg.create(coordination_id="c2", coordinator_agent_id="a1", target_agent_id="a2",
               objective="send", tasks=(CoordinationTask("t1", "Send", "email.send"),))
    result = reg.assess("c2")
    assert result.status == CoordinationStatus.BLOCKED
    assert result.dispositions["t1"] == CoordinationDisposition.CAPABILITY_UNAVAILABLE


def test_deny_or_scope_mismatch_becomes_advisory_not_authority():
    _, perms, reg = setup()
    perms.grant(permission_id="p2", agent_id="a2", capability="notify", scope={"channel": "ops"})
    reg.create(coordination_id="c3", coordinator_agent_id="a1", target_agent_id="a2",
               objective="notify", tasks=(CoordinationTask("t1", "Notify", "notify", {"channel": "customer"}),))
    result = reg.assess("c3")
    assert result.status == CoordinationStatus.BLOCKED
    assert result.dispositions["t1"] == CoordinationDisposition.ADVISORY_ONLY


def test_self_delegation_and_cross_tenant_construction_are_blocked():
    _, perms, _ = setup()
    with pytest.raises(ValueError, match=CoordinationBlock.SELF_DELEGATION.value):
        MultiAgentCoordinationRegistry("ws-1", _.identity_registry, perms).create(
            coordination_id="bad", coordinator_agent_id="a1", target_agent_id="a1",
            objective="x", tasks=(CoordinationTask("t", "x", "crm.read"),))
    ids2 = AgentIdentityRegistry("ws-2")
    with pytest.raises(ValueError, match=CoordinationBlock.TENANT_MISMATCH.value):
        MultiAgentCoordinationRegistry("ws-1", ids2, perms)


def test_dependency_cycles_are_blocked():
    _, _, reg = setup()
    tasks = (CoordinationTask("a", "A", "crm.read", depends_on=("b",)),
             CoordinationTask("b", "B", "crm.read", depends_on=("a",)))
    with pytest.raises(ValueError, match=CoordinationBlock.INVALID_COORDINATION.value):
        reg.create(coordination_id="c4", coordinator_agent_id="a1", target_agent_id="a2",
                   objective="cycle", tasks=tasks)


def test_tamper_and_terminal_replay_are_blocked():
    _, _, reg = setup()
    reg.create(coordination_id="c5", coordinator_agent_id="a1", target_agent_id="a2",
               objective="x", tasks=(CoordinationTask("t", "T", "crm.read", {"workspace_id": "ws-1"}),))
    req = reg.get("c5")
    object.__setattr__(req, "objective", "tampered")
    with pytest.raises(ValueError, match=CoordinationBlock.COORDINATION_TAMPERED.value):
        reg.assess("c5")
    # rebuild clean registry for terminal transition check
    _, _, reg2 = setup()
    reg2.create(coordination_id="c6", coordinator_agent_id="a1", target_agent_id="a2",
                objective="x", tasks=(CoordinationTask("t", "T", "crm.read", {"workspace_id": "ws-1"}),))
    reg2.assess("c6")
    reg2.complete("c6")
    with pytest.raises(ValueError, match=CoordinationBlock.TERMINAL.value):
        reg2.complete("c6")


def test_cancel_is_terminal_and_context_is_advisory():
    _, _, reg = setup()
    reg.create(coordination_id="c7", coordinator_agent_id="a1", target_agent_id="a2",
               objective="x", tasks=(CoordinationTask("t", "T", "crm.read"),))
    req = reg.cancel("c7")
    assert req.status == CoordinationStatus.CANCELLED
    ctx = reg.coordination_context("c7")
    assert ctx["advisory_only"] is True
    assert ctx["authority_granted"] is False


def test_active_agent_integrity_is_required_for_listing():
    ids, _, reg = setup()
    reg.create(coordination_id="c8", coordinator_agent_id="a1", target_agent_id="a2",
               objective="x", tasks=(CoordinationTask("t", "T", "crm.read"),))
    ids.transition(agent_id="a1", status=AgentStatus.SUSPENDED)
    with pytest.raises(ValueError, match=CoordinationBlock.AGENT_NOT_ACTIVE.value):
        reg.list_requests(agent_id="a1")
