from datetime import datetime, timezone

import pytest

from organism.memory import Experience, Memory, MemoryKind, MemoryStore, Outcome


def test_memory_is_workspace_scoped_and_bounded():
    store = MemoryStore(max_memories_per_workspace=2)
    for i in range(3):
        store.remember(Memory("w1", MemoryKind.EPISODIC, {"i": i}))
    store.remember(Memory("w2", MemoryKind.SEMANTIC, {"i": 9}))

    assert len(store.recent("w1")) == 2
    assert store.recent("w1")[0].content["i"] == 1
    assert len(store.recent("w2")) == 1


def test_experience_becomes_episodic_memory_with_outcome():
    experience = Experience(
        workspace_id="w1",
        goal_id="goal_1",
        action="publish",
        outcome=Outcome.PARTIAL,
        result={"published": 1, "failed": 1},
        lesson="retry only the failed destination",
    )
    memory = experience.to_memory()
    assert memory.kind is MemoryKind.EPISODIC
    assert memory.content["outcome"] == "partial"
    assert memory.content["lesson"] == "retry only the failed destination"


def test_memory_rejects_naive_timestamp_and_bad_confidence():
    with pytest.raises(ValueError):
        Memory("w1", MemoryKind.SEMANTIC, {}, occurred_at=datetime.now())
    with pytest.raises(ValueError):
        Memory("w1", MemoryKind.SEMANTIC, {}, confidence=1.1)


def test_memory_queries_are_bounded_and_filterable():
    store = MemoryStore()
    store.remember(Memory("w1", MemoryKind.PROCEDURAL, {"rule": "a"}, tags=("safe",)))
    store.remember(Memory("w1", MemoryKind.SEMANTIC, {"fact": "b"}, tags=("context",)))
    assert len(store.recent("w1", kind=MemoryKind.PROCEDURAL, tags={"safe"}, limit=1)) == 1
    assert store.status("w1")["procedural"] == 1
