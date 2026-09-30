import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_6_agent_budgets import AgentBudgetRegistry, BudgetBlock, BudgetStatus

def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="Worker", purpose="work", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    return ids, AgentBudgetRegistry("ws-1", ids)

def test_budget_bounds_usage_without_granting_authority():
    _, reg = setup()
    b = reg.create(budget_id="b1", agent_id="a1", max_actions=10, max_cost=5, max_risk_units=3)
    assert b.budget_fingerprint == b.fingerprint()
    assert reg.check("b1", actions=5, cost=2, risk_units=1).allowed
    updated = reg.record_usage("b1", actions=5, cost=2, risk_units=1)
    assert updated.used_actions == 5
    assert reg.check("b1", actions=6).allowed is False
    assert reg.check("b1").authority_granted is False

def test_exhaustion_is_fail_closed():
    _, reg = setup()
    reg.create(budget_id="b2", agent_id="a1", max_actions=2)
    b = reg.record_usage("b2", actions=2)
    assert b.status == BudgetStatus.EXHAUSTED
    assert not reg.check("b2", actions=1).allowed

def test_invalid_and_negative_usage_rejected():
    _, reg = setup()
    reg.create(budget_id="b3", agent_id="a1", max_cost=10)
    with pytest.raises(ValueError, match=BudgetBlock.NEGATIVE_DELTA.value):
        reg.check("b3", cost=-1)
    with pytest.raises(ValueError, match=BudgetBlock.INVALID_LIMIT.value):
        reg.create(budget_id="b4", agent_id="a1")

def test_tamper_and_tenant_protection():
    ids, reg = setup()
    b = reg.create(budget_id="b5", agent_id="a1", max_actions=3)
    tampered = type(b)(**{**b.__dict__, "used_actions": 99})
    with pytest.raises(ValueError, match=BudgetBlock.BUDGET_TAMPERED.value): reg.assert_integrity(tampered)
    other = AgentIdentityRegistry("ws-2")
    with pytest.raises(ValueError, match=BudgetBlock.TENANT_MISMATCH.value): AgentBudgetRegistry("ws-1", other)

def test_suspended_budget_is_not_admitted():
    _, reg = setup()
    reg.create(budget_id="b6", agent_id="a1", max_actions=5)
    reg.set_status("b6", BudgetStatus.SUSPENDED)
    result = reg.check("b6", actions=1)
    assert result.allowed is False
    assert BudgetBlock.SUSPENDED.value in result.reasons
