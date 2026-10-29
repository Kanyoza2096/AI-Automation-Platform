from organism.world_model import BeliefStatus, WorldFact, WorldModel


def test_world_model_preserves_contradiction_provenance():
    model = WorldModel()
    first = model.observe(WorldFact("ws", "campaign", "status", "active", source="scheduler"))
    second = model.observe(WorldFact("ws", "campaign", "status", "paused", source="operator"))

    current = model.get("ws", "campaign", "status")
    assert current.value == "paused"
    assert current.conflicts_with == (first.fact_id,)
    history = model.history("ws", "campaign", "status")
    assert any(item.fact_id == first.fact_id and item.status is BeliefStatus.CONTRADICTED for item in history)
    assert any(item.fact_id == second.fact_id for item in history)


def test_world_model_keeps_history_bounded():
    model = WorldModel()
    for i in range(30):
        model.observe(WorldFact("ws", "x", "state", i))
    assert len(model.history("ws", "x", "state")) <= 20
