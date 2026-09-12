from organism.learning import ExecutionFeedback, LearningEngine
from organism.memory import MemoryStore, Outcome
from organism.prediction import PredictiveCausalEngine
from organism.world_model import BeliefStatus, WorldModel
from services.execution_ledger import ExecutionLedger


def _learn(learner, status):
    ledger = ExecutionLedger(allow_in_memory=True)
    record = ledger.begin(workspace_id="w1", action="publish", idempotency_key=f"k-{status}", provider="meta")
    record = ledger.claim(record)
    record = ledger.complete(record, status=status)
    return learner.learn_from_record(record)


def test_prediction_is_bounded_and_persisted_as_predicted_belief():
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    _learn(learner, "succeeded")
    _learn(learner, "succeeded")
    _learn(learner, "failed")

    prediction = PredictiveCausalEngine(memory, world).predict_action("w1", "publish")
    assert prediction.predicted_outcome is Outcome.SUCCESS
    assert 0 < prediction.probability < 1
    assert prediction.sample_size == 3
    fact = world.get("w1", "action:publish", "predicted_outcome")
    assert fact.status is BeliefStatus.PREDICTED
    assert fact.value == "success"


def test_uncertain_experience_is_excluded_from_prediction_and_causal_sample():
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    _learn(learner, "uncertain")

    engine = PredictiveCausalEngine(memory, world)
    prediction = engine.predict_action("w1", "publish")
    hypothesis = engine.causal_hypothesis("w1", "publish")
    assert prediction.sample_size == 0
    assert prediction.confidence == 0.1
    assert hypothesis.sample_size == 0
    assert hypothesis.confidence == 0.0


def test_causal_output_is_explicitly_a_hypothesis_not_authority():
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    for i in range(5):
        _learn(learner, "succeeded")
    hypothesis = PredictiveCausalEngine(memory, world).causal_hypothesis("w1", "publish")
    assert hypothesis.direction == "positive"
    assert hypothesis.strength == 1.0
    assert "not established" in hypothesis.caveat


def test_prediction_is_tenant_isolated():
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    _learn(learner, "succeeded")
    engine = PredictiveCausalEngine(memory, world)
    other = engine.predict_action("w2", "publish")
    assert other.sample_size == 0
    assert world.get("w2", "action:publish", "predicted_outcome").value == "success"
    assert world.get("w1", "action:publish", "predicted_outcome") is None


def test_prediction_is_contextual_when_contextual_experience_exists():
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    for i, status in enumerate(("succeeded", "succeeded", "failed")):
        ledger = ExecutionLedger(allow_in_memory=True)
        record = ledger.begin(workspace_id="w1", action="publish", idempotency_key=f"ctx-{i}", provider="meta")
        record = ledger.claim(record)
        record = ledger.complete(record, status=status, evidence={"context": {"platform": "facebook", "content_type": "video"}})
        learner.learn_from_record(record)
    ledger = ExecutionLedger(allow_in_memory=True)
    record = ledger.begin(workspace_id="w1", action="publish", idempotency_key="ctx-4", provider="meta")
    record = ledger.claim(record)
    record = ledger.complete(record, status="failed", evidence={"context": {"platform": "facebook", "content_type": "image"}})
    learner.learn_from_record(record)

    engine = PredictiveCausalEngine(memory, world)
    video = engine.predict_action("w1", "publish", context={"platform": "facebook", "content_type": "video"})
    image = engine.predict_action("w1", "publish", context={"platform": "facebook", "content_type": "image"})
    assert video.contextual_sample_size == 3
    assert image.contextual_sample_size == 1
    assert video.predicted_outcome is Outcome.SUCCESS
    assert image.predicted_outcome is Outcome.FAILURE
    assert video.context_key != image.context_key


def test_prediction_falls_back_to_action_history_for_unseen_context():
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    _learn(learner, "succeeded")
    prediction = PredictiveCausalEngine(memory, world).predict_action(
        "w1", "publish", context={"platform": "instagram", "content_type": "reel"}
    )
    assert prediction.sample_size == 1
    assert prediction.contextual_sample_size == 0
    assert prediction.context_key == "default"


def test_prediction_calibrator_measures_realized_accuracy_and_ece():
    from organism.prediction import PredictionCalibrator
    memory, world = MemoryStore(), WorldModel()
    learner = LearningEngine(memory, world)
    _learn(learner, "succeeded")
    _learn(learner, "succeeded")
    prediction = PredictiveCausalEngine(memory, world).predict_action("w1", "publish")
    calibrator = PredictionCalibrator(history_limit=10)
    calibrator.record(prediction, Outcome.SUCCESS)
    calibrator.record(prediction, Outcome.FAILURE)
    report = calibrator.evaluate(workspace_id="w1", action="publish")
    assert report.sample_size == 2
    assert report.accuracy == 0.5
    assert report.brier_score is not None
    assert report.expected_calibration_error is not None


def test_prediction_calibrator_rejects_uncertain_realized_outcome():
    from organism.prediction import PredictionCalibrator
    memory, world = MemoryStore(), WorldModel()
    prediction = PredictiveCausalEngine(memory, world).predict_action("w1", "publish")
    calibrator = PredictionCalibrator()
    import pytest
    with pytest.raises(ValueError):
        calibrator.record(prediction, Outcome.UNCERTAIN)


def test_prediction_calibrator_is_context_aware_and_bounded():
    from organism.prediction import PredictionCalibrator
    from organism.prediction import Prediction
    from datetime import datetime, timezone
    calibrator = PredictionCalibrator(history_limit=2)
    for context in ("a", "b", "c"):
        prediction = Prediction("w1", "publish", Outcome.SUCCESS, 0.75, 0.8, 3, "test", datetime.now(timezone.utc), context_key=context)
        calibrator.record(prediction, Outcome.SUCCESS)
    report = calibrator.evaluate(workspace_id="w1")
    assert report.sample_size == 2
    assert report.context_sample_sizes == {"b": 1, "c": 1}
