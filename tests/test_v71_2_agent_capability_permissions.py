import pytest

from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_2_agent_capability_permissions import (
    AgentCapabilityPermission, AgentCapabilityPermissionRegistry, PermissionEffect,
)


def setup():
    identities = AgentIdentityRegistry("w1")
    identities.register(
        agent_id="sales",
        name="Sales",
        purpose="Sales work",
        declared_capabilities=("crm.read", "crm.write"),
    )
    identities.transition(agent_id="sales", status=AgentStatus.ACTIVE)
    return identities, AgentCapabilityPermissionRegistry("w1", identities)


def test_active_identity_never_becomes_authority():
    identities, perms = setup()
    assert identities.can_authorize("sales") is False
    assert identities.can_execute("sales") is False
    assert perms.can_request(agent_id="sales", capability="crm.read") is False


def test_default_deny_and_declared_capability_fence():
    _, perms = setup()
    assert not perms.can_request(agent_id="sales", capability="crm.read")
    with pytest.raises(ValueError, match="capability_not_declared"):
        perms.grant(permission_id="p1", agent_id="sales", capability="publish_content")


def test_scoped_allow_is_not_authority():
    _, perms = setup()
    p = perms.grant(permission_id="p1", agent_id="sales", capability="crm.read", scope={"object": "contact"})
    assert p.effect == PermissionEffect.ALLOW
    assert perms.can_request(agent_id="sales", capability="crm.read", requested_scope={"object": "contact"})
    assert not perms.can_request(agent_id="sales", capability="crm.read", requested_scope={"object": "deal"})


def test_explicit_deny_wins_over_allow():
    _, perms = setup()
    perms.grant(permission_id="allow", agent_id="sales", capability="crm.write")
    perms.deny(permission_id="deny", agent_id="sales", capability="crm.write", scope={"object": "deal"})
    assert perms.can_request(agent_id="sales", capability="crm.write", requested_scope={"object": "contact"})
    assert not perms.can_request(agent_id="sales", capability="crm.write", requested_scope={"object": "deal"})


def test_inactive_agent_cannot_request_even_with_permission():
    identities, perms = setup()
    perms.grant(permission_id="p1", agent_id="sales", capability="crm.read")
    identities.transition(agent_id="sales", status=AgentStatus.SUSPENDED)
    ok, reasons = perms.check(agent_id="sales", capability="crm.read")
    assert not ok and reasons == ("agent_not_active",)


def test_tamper_and_workspace_fences():
    _, perms = setup()
    p = perms.grant(permission_id="p1", agent_id="sales", capability="crm.read")
    tampered = AgentCapabilityPermission(**{**p.__dict__, "scope": {"object": "deal"}})
    with pytest.raises(ValueError, match="permission_tampered"):
        perms.assert_integrity(tampered)
    foreign = AgentCapabilityPermission(**{**p.__dict__, "workspace_id": "w2"})
    with pytest.raises(ValueError, match="tenant_mismatch"):
        perms.assert_integrity(foreign)
