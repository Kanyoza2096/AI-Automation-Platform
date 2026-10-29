from services.v71_27_worker_learning_feedback import WorkerLearningFeedbackRegistry, FeedbackBlock

def test_phase716_module_is_importable_and_declares_safe_boundary():
    assert WorkerLearningFeedbackRegistry.VERSION == "phase7.16"
    assert FeedbackBlock.HISTORICAL_OUTCOME_MUTATION.value == "historical_outcome_mutation"

def test_phase716_score_bounds_are_explicit():
    assert WorkerLearningFeedbackRegistry._score(0) == 0.0
    assert WorkerLearningFeedbackRegistry._score(1) == 1.0
    for value in (-0.1, 1.1):
        try: WorkerLearningFeedbackRegistry._score(value)
        except ValueError as exc: assert str(exc) == "invalid_score"
        else: raise AssertionError("out-of-range score accepted")
