from organism.dreaming import DreamEngine
from organism.goals import Goal
from organism.learning import LearningEngine
from organism.memory import MemoryStore
from organism.prediction import PredictiveCausalEngine
from organism.world_model import WorldModel
from services.execution_ledger import ExecutionLedger


def _learn(memory, world, action, status):
    ledger = ExecutionLedger(allow_in_memory=True)
    r = ledger.begin(workspace_id="w1", action=action, idempotency_key=f"{action}-{status}-{id(status)}", provider="meta")
    r = ledger.claim(r)
    r = ledger.complete(r, status=status)
    return LearningEngine(memory, world).learn_from_record(r)


def test_dream_is_read_only_against_world_model():
    memory, world = MemoryStore(), WorldModel()
    _learn(memory, world, "publish", "succeeded")
    before = world.status("w1")
    dream = DreamEngine(memory, world).simulate("w1", ["publish"])
    after = world.status("w1")
    assert dream.production_mutated is False
    assert before == after


def test_dream_uses_tenant_scoped_predictions():
    memory, world = MemoryStore(), WorldModel()
    _learn(memory, world, "publish", "succeeded")
    dream = DreamEngine(memory, world).simulate("w2", ["publish"])
    assert dream.workspace_id == "w2"
    assert dream.steps[0].probability < 1
    assert world.get("w2", "action:publish", "predicted_outcome") is None


def test_uncertain_is_not_promoted_by_dreaming():
    memory, world = MemoryStore(), WorldModel()
    _learn(memory, world, "publish", "uncertain")
    dream = DreamEngine(memory, world).simulate("w1", ["publish"])
    assert dream.steps[0].predicted_outcome.value == "success"  # prior smoothing only
    assert dream.confidence == 0.1
    assert dream.production_mutated is False


def test_goal_is_evaluated_but_not_mutated():
    memory, world = MemoryStore(), WorldModel()
    goal = Goal("w1", "publish", "post exists", progress=0.4)
    dream = DreamEngine(memory, world).simulate("w1", ["publish"], goal=goal)
    assert dream.goal_id == goal.goal_id
    assert goal.progress == 0.4
    assert f"goal:{goal.goal_id}" in dream.assumptions[-1]


def test_cross_tenant_goal_rejected():
    memory, world = MemoryStore(), WorldModel()
    goal = Goal("other", "publish", "post exists")
    try:
        DreamEngine(memory, world).simulate("w1", ["publish"], goal=goal)
        assert False
    except ValueError as exc:
        assert "workspace_id" in str(exc)


def test_compare_orders_hypothetical_candidates_without_execution():
    memory, world = MemoryStore(), WorldModel()
    for _ in range(5):
        _learn(memory, world, "good", "succeeded")
        _learn(memory, world, "bad", "failed")
    dreams = DreamEngine(memory, world).compare("w1", [["bad"], ["good"]])
    assert dreams[0].steps[0].action == "good"
    assert all(not d.production_mutated for d in dreams)


def test_dream_is_bounded():
    memory, world = MemoryStore(), WorldModel()
    try:
        DreamEngine(memory, world).simulate("w1", ["x"] * 21)
        assert False
    except ValueError as exc:
        assert "20" in str(exc)
