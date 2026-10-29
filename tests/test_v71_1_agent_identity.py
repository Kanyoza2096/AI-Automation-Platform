import pytest
from services.v71_1_agent_identity import *


def test_registration_is_tenant_scoped_and_deterministic():
    r = AgentIdentityRegistry("w1")
    a = r.register(agent_id="agent.sales", name="Sales Worker", purpose="Support sales operations", declared_capabilities=("crm.read", "crm.read"))
    assert a.workspace_id == "w1"
    assert a.declared_capabilities == ("crm.read",)
    assert a.identity_fingerprint == a.fingerprint()


def test_duplicate_and_invalid_identity_fences():
    r = AgentIdentityRegistry("w1")
    r.register(agent_id="a1", name="A", purpose="P")
    with pytest.raises(ValueError, match="duplicate_agent"):
        r.register(agent_id="a1", name="A2", purpose="P2")
    with pytest.raises(ValueError, match="invalid_identity"):
        r.register(agent_id="", name="A", purpose="P")


def test_identity_does_not_grant_execution_authority():
    r = AgentIdentityRegistry("w1")
    a = r.register(agent_id="a1", name="A", purpose="P", declared_capabilities=("publish_content",))
    assert a.status == AgentStatus.PROPOSED
    assert not r.can_execute("a1")
    r.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    # Active identity is eligibility only; execution authority remains downstream in governance/Action Kernel.
    assert not r.can_execute("a1")
    # Active identity is only an eligibility signal; no permission/Action Kernel call exists here.
    assert r.require_active("a1").declared_capabilities == ("publish_content",)


def test_lifecycle_is_monotonic_and_retirement_terminal():
    r = AgentIdentityRegistry("w1")
    r.register(agent_id="a1", name="A", purpose="P")
    r.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    r.transition(agent_id="a1", status=AgentStatus.SUSPENDED)
    with pytest.raises(ValueError, match="agent_not_active"):
        r.require_active("a1")
    r.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    r.transition(agent_id="a1", status=AgentStatus.RETIRED)
    with pytest.raises(ValueError, match="agent_retired"):
        r.require_active("a1")
    with pytest.raises(ValueError, match="invalid_transition"):
        r.transition(agent_id="a1", status=AgentStatus.ACTIVE)


def test_tamper_and_cross_tenant_fences():
    r = AgentIdentityRegistry("w1")
    a = r.register(agent_id="a1", name="A", purpose="P")
    tampered = AgentIdentity(**{**a.__dict__, "purpose": "changed"})
    with pytest.raises(ValueError, match="identity_tampered"):
        r.assert_integrity(tampered)
    foreign = AgentIdentity(**{**a.__dict__, "workspace_id": "w2"})
    with pytest.raises(ValueError, match="tenant_mismatch"):
        r.assert_integrity(foreign)
