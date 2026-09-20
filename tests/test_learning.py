from organism.goals import Goal
from organism.learning import ExecutionFeedback, LearningEngine
from organism.memory import MemoryStore, Outcome
from organism.world_model import BeliefStatus, WorldModel
from services.execution_ledger import ExecutionLedger


def _record(status="succeeded"):
    ledger = ExecutionLedger(allow_in_memory=True)
    record = ledger.begin(workspace_id="w1", action="publish", idempotency_key="k1", provider="meta")
    record = ledger.claim(record)
    return ledger.complete(record, status=status, provider_object_id="post-1", evidence={"trace": "x"})


def test_execution_receipt_becomes_experience_memory_and_world_belief():
    memory = MemoryStore()
    world = WorldModel()
    learner = LearningEngine(memory, world)
    goal = Goal("w1", "publish", "post exists", progress=0.2)

    result = learner.learn_from_record(_record(), goal=goal, result={"goal_complete": True})

    assert result.experience.outcome is Outcome.SUCCESS
    assert result.memory.content["result"]["execution_id"] == result.experience.result["execution_id"]
    assert result.goal_progress == 1.0
    fact = world.get("w1", "execution:" + result.experience.result["execution_id"], "status")
    assert fact.status is BeliefStatus.KNOWN
    assert fact.value == "success"


def test_uncertain_execution_never_becomes_success_or_goal_completion():
    memory = MemoryStore()
    world = WorldModel()
    learner = LearningEngine(memory, world)
    goal = Goal("w1", "publish", "post exists", progress=0.2)

    result = learner.learn_from_record(_record("uncertain"), goal=goal, result={"goal_complete": True, "goal_progress": 1})

    assert result.experience.outcome is Outcome.UNCERTAIN
    assert result.goal_progress is None
    fact = world.get("w1", "execution:" + result.experience.result["execution_id"], "status")
    assert fact.status is BeliefStatus.UNCERTAIN


def test_learning_rejects_cross_tenant_goal():
    learner = LearningEngine(MemoryStore(), WorldModel())
    goal = Goal("other", "publish", "done")
    try:
        learner.learn_from_record(_record(), goal=goal)
        assert False, "expected tenant mismatch"
    except ValueError as exc:
        assert "workspace_id" in str(exc)


def test_learning_deduplicates_replayed_execution_receipts():
    memory = MemoryStore()
    world = WorldModel()
    learner = LearningEngine(memory, world)
    record = _record()
    first = learner.learn_from_record(record)
    second = learner.learn_from_record(record)
    assert first.memory.memory_id == second.memory.memory_id
    assert memory.status("w1")["episodic"] == 1


def test_learning_preserves_goal_identity_from_feedback():
    memory = MemoryStore()
    world = WorldModel()
    learner = LearningEngine(memory, world)
    goal = Goal("w1", "publish", "post exists", progress=0.2)
    feedback = __import__("organism.learning", fromlist=["ExecutionFeedback"]).ExecutionFeedback(
        workspace_id="w1", execution_id="ex-goal", action="publish", outcome=Outcome.SUCCESS,
        provider="meta", provider_object_id="post-1", provider_request_id=None,
        evidence={"receipt": "authoritative"}, error=None,
        occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        confidence=1.0, goal_id=goal.goal_id,
    )
    result = learner.learn(feedback, goal=goal, result={"goal_complete": True})
    assert result.experience.goal_id == goal.goal_id
