import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_7_human_takeover import HumanTakeoverRegistry, TakeoverBlock, TakeoverDisposition, TakeoverStatus

def setup():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="Worker", purpose="work")
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    return ids, HumanTakeoverRegistry("ws-1", ids)

def test_takeover_pauses_and_never_grants_authority():
    _, reg = setup()
    c = reg.create(case_id="c1", agent_id="a1", reason="high risk", trigger="risk_threshold", context={"x": 1})
    assert c.status == TakeoverStatus.REQUESTED
    c = reg.claim("c1", human_id="human-1")
    c = reg.resolve("c1", human_id="human-1", disposition=TakeoverDisposition.RESUME_TO_GOVERNANCE)
    assert c.status == TakeoverStatus.RESOLVED
    ctx = reg.handoff_context("c1")
    assert ctx["authority_granted"] is False
    assert ctx["advisory_only"] is True

def test_only_claimed_human_can_resolve():
    _, reg = setup()
    reg.create(case_id="c2", agent_id="a1", reason="uncertainty", trigger="low_confidence")
    with pytest.raises(ValueError, match=TakeoverBlock.INVALID_HUMAN.value):
        reg.resolve("c2", human_id="human-1", disposition=TakeoverDisposition.REJECT)
    reg.claim("c2", human_id="human-1")
    with pytest.raises(ValueError, match=TakeoverBlock.INVALID_HUMAN.value):
        reg.resolve("c2", human_id="human-2", disposition=TakeoverDisposition.REJECT)

def test_terminal_case_cannot_be_replayed_or_cancelled():
    _, reg = setup()
    reg.create(case_id="c3", agent_id="a1", reason="review", trigger="policy")
    reg.claim("c3", human_id="human-1")
    reg.resolve("c3", human_id="human-1", disposition=TakeoverDisposition.REJECT)
    with pytest.raises(ValueError, match=TakeoverBlock.INVALID_TRANSITION.value):
        reg.claim("c3", human_id="human-1")
    with pytest.raises(ValueError, match=TakeoverBlock.TERMINAL.value):
        reg.cancel("c3")

def test_tenant_and_tamper_protection():
    ids, reg = setup()
    c = reg.create(case_id="c4", agent_id="a1", reason="review", trigger="policy")
    tampered = type(c)(**{**c.__dict__, "reason": "changed"})
    with pytest.raises(ValueError, match=TakeoverBlock.CASE_TAMPERED.value): reg.assert_integrity(tampered)
    other = AgentIdentityRegistry("ws-2")
    with pytest.raises(ValueError, match=TakeoverBlock.TENANT_MISMATCH.value): HumanTakeoverRegistry("ws-1", other)

def test_pending_is_explicit_and_agent_must_be_active():
    ids, reg = setup()
    reg.create(case_id="c5", agent_id="a1", reason="review", trigger="human_required")
    assert [x.case_id for x in reg.pending(agent_id="a1")] == ["c5"]
    ids.transition(agent_id="a1", status=AgentStatus.SUSPENDED)
    with pytest.raises(ValueError, match=TakeoverBlock.AGENT_NOT_ACTIVE.value): reg.create(case_id="c6", agent_id="a1", reason="x", trigger="y")
