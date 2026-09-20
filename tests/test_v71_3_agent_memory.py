from datetime import datetime, timedelta, timezone
import pytest
from services.v71_1_agent_identity import AgentIdentityRegistry, AgentStatus
from services.v71_3_agent_memory import AgentMemoryRegistry, MemoryKind, MemoryBlock, MemoryStatus


def active_registry():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="Sales Worker", purpose="sales", declared_capabilities=("crm.read",))
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    return ids


def test_memory_requires_active_agent_and_is_advisory():
    ids = AgentIdentityRegistry("ws-1")
    ids.register(agent_id="a1", name="Worker", purpose="test")
    mem = AgentMemoryRegistry("ws-1", ids)
    with pytest.raises(ValueError, match=MemoryBlock.AGENT_NOT_ACTIVE.value):
        mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.FACT, subject="lead", content="hot", source="crm")
    ids.transition(agent_id="a1", status=AgentStatus.ACTIVE)
    mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.FACT, subject="lead", content="hot", source="crm")
    ctx = mem.usable_context(agent_id="a1")
    assert ctx[0]["advisory"] is True
    assert "approved" not in ctx[0] and "authority" not in ctx[0]


def test_tenant_and_integrity_fences():
    ids = active_registry()
    mem = AgentMemoryRegistry("ws-1", ids)
    m = mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.OBSERVATION, subject="x", content="y", source="event")
    tampered = type(m)(**{**m.__dict__, "content": "evil"})
    with pytest.raises(ValueError, match=MemoryBlock.MEMORY_TAMPERED.value):
        mem.assert_integrity(tampered)
    with pytest.raises(ValueError, match=MemoryBlock.TENANT_MISMATCH.value):
        AgentMemoryRegistry("ws-2", ids)


def test_revoked_and_expired_memory_never_recalled():
    ids = active_registry(); mem = AgentMemoryRegistry("ws-1", ids)
    expiry = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    mem.remember(memory_id="expired", agent_id="a1", kind=MemoryKind.FACT, subject="x", content="old", source="crm", expires_at=expiry)
    mem.remember(memory_id="live", agent_id="a1", kind=MemoryKind.FACT, subject="x", content="new", source="crm")
    mem.revoke("live")
    assert mem.recall(agent_id="a1") == ()


def test_conflicting_memories_are_not_silently_promoted():
    ids = active_registry(); mem = AgentMemoryRegistry("ws-1", ids)
    mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.FACT, subject="deal", content="open", source="crm")
    mem.remember(memory_id="m2", agent_id="a1", kind=MemoryKind.FACT, subject="deal", content="closed", source="crm")
    assert mem.recall(agent_id="a1") == ()
    recalled = mem.recall(agent_id="a1", include_conflicts=True)
    assert len(recalled) == 2 and all(r.conflict_ids for r in recalled)


def test_memory_is_not_an_authority_plane():
    ids = active_registry(); mem = AgentMemoryRegistry("ws-1", ids)
    mem.remember(memory_id="m1", agent_id="a1", kind=MemoryKind.DECISION_CONTEXT, subject="publish", content="approved by customer", source="crm")
    payload = mem.usable_context(agent_id="a1")[0]
    forbidden = {"execute", "action", "permission", "approved", "authority", "provider"}
    assert not (forbidden & set(payload))
