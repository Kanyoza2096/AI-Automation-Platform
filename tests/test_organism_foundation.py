from datetime import datetime, timedelta, timezone

import pytest

from organism.goals import Goal, GoalPriority, GoalState
from organism.world_model import BeliefStatus, WorldFact, WorldModel


def test_world_model_is_workspace_scoped_and_revisioned():
    model = WorldModel(max_facts_per_workspace=10)
    first = model.observe(WorldFact("w1", "account", "status", "active"))
    second = model.observe(WorldFact("w1", "account", "status", "paused", status=BeliefStatus.INFERRED, confidence=0.7))
    other = model.observe(WorldFact("w2", "account", "status", "active"))

    assert first.revision == 1
    assert second.revision == 2
    assert other.revision == 1
    assert model.get("w1", "account", "status").value == "paused"
    assert model.get("w2", "account", "status").value == "active"


def test_world_fact_expiry_is_exposed_as_stale():
    model = WorldModel()
    now = datetime.now(timezone.utc)
    model.observe(WorldFact("w1", "provider", "available", True, expires_at=now - timedelta(seconds=1)))

    fact = model.get("w1", "provider", "available")
    assert fact.status == BeliefStatus.STALE


def test_world_fact_rejects_invalid_confidence():
    with pytest.raises(ValueError):
        WorldFact("w1", "a", "b", 1, confidence=1.1)


def test_goal_is_bounded_and_tracks_overdue_state():
    goal = Goal(
        workspace_id="w1",
        title="Increase meaningful engagement",
        success_condition="engagement >= 120",
        priority=GoalPriority.HIGH,
        deadline=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert goal.is_overdue()
    achieved = goal.with_progress(1.5)
    assert achieved.progress == 1.0
    assert achieved.state == GoalState.ACHIEVED


def test_goal_rejects_naive_deadline():
    with pytest.raises(ValueError):
        Goal("w1", "goal", "condition", deadline=datetime.now())
