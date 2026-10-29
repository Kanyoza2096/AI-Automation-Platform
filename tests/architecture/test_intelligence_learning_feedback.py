import pytest
from services.intelligence_learning_feedback import (
    IntelligenceLearningFeedbackEngine,
    IntelligenceLearningFeedbackStore,
    validate_learning_feedback,
)


def _attribution():
    return {
        "workspace_id": "ws-1",
        "execution_id": "exe-1",
        "outcome_id": "out-1",
        "evidence_fingerprint": "attr-fp",
        "authoritative_outcome_source": "verification_and_reconciliation",
        "causal_claim": False,
        "action_authority_granted": False,
    }


def test_verified_success_becomes_positive_advisory_feedback():
    result = IntelligenceLearningFeedbackEngine().derive(
        workspace_id="ws-1", attribution=_attribution(),
        outcome_measurement={"workspace_id":"ws-1", "measured_success":True, "confidence":0.9},
    )
    assert result.learning_state == "positive"
    assert result.to_dict()["authority"] == "none"
    assert result.to_dict()["causal_claim"] is False


def test_verified_failure_becomes_negative_feedback():
    result = IntelligenceLearningFeedbackEngine().derive(
        workspace_id="ws-1", attribution=_attribution(),
        outcome_measurement={"measured_success":False, "confidence":0.9},
    )
    assert result.learning_state == "negative"


def test_low_confidence_never_becomes_positive():
    result = IntelligenceLearningFeedbackEngine().derive(
        workspace_id="ws-1", attribution=_attribution(),
        outcome_measurement={"measured_success":True, "confidence":0.49},
    )
    assert result.learning_state == "uncertain"


def test_missing_measurement_is_uncertain():
    result = IntelligenceLearningFeedbackEngine().derive(
        workspace_id="ws-1", attribution=_attribution(), outcome_measurement={}
    )
    assert result.learning_state == "uncertain"


def test_non_authoritative_attribution_is_rejected():
    a = _attribution(); a["authoritative_outcome_source"] = "intelligence"
    with pytest.raises(ValueError, match="authoritative verification"):
        IntelligenceLearningFeedbackEngine().derive(workspace_id="ws-1", attribution=a, outcome_measurement={})


def test_causal_attribution_is_rejected():
    a = _attribution(); a["causal_claim"] = True
    with pytest.raises(ValueError, match="non-causal"):
        IntelligenceLearningFeedbackEngine().derive(workspace_id="ws-1", attribution=a, outcome_measurement={})


def test_workspace_isolation_is_enforced():
    with pytest.raises(PermissionError, match="workspace mismatch"):
        IntelligenceLearningFeedbackEngine().derive(
            workspace_id="ws-1", attribution=_attribution(),
            outcome_measurement={"workspace_id":"ws-2", "measured_success":True, "confidence":1.0},
        )


def test_learning_feedback_is_deterministic():
    engine = IntelligenceLearningFeedbackEngine()
    kwargs = dict(workspace_id="ws-1", attribution=_attribution(), outcome_measurement={"measured_success":True,"confidence":0.9})
    assert engine.derive(**kwargs).evidence_fingerprint == engine.derive(**kwargs).evidence_fingerprint


def test_store_survives_restart(tmp_path):
    path = tmp_path / "learning.sqlite"
    engine = IntelligenceLearningFeedbackEngine()
    feedback = engine.derive(workspace_id="ws-1", attribution=_attribution(), outcome_measurement={"measured_success":True,"confidence":0.9})
    store = IntelligenceLearningFeedbackStore(path); store.append(feedback); store.close()
    reopened = IntelligenceLearningFeedbackStore(path)
    rows = reopened.load_workspace("ws-1")
    assert len(rows) == 1
    assert rows[0]["feedback_id"] == feedback.feedback_id
    reopened.close()


def test_duplicate_append_is_idempotent():
    store = IntelligenceLearningFeedbackStore()
    feedback = IntelligenceLearningFeedbackEngine().derive(workspace_id="ws-1", attribution=_attribution(), outcome_measurement={"measured_success":True,"confidence":0.9})
    store.append(feedback); store.append(feedback)
    assert len(store.load_workspace("ws-1")) == 1
    store.close()


def test_validation_rejects_authority_flags():
    feedback = IntelligenceLearningFeedbackEngine().derive(workspace_id="ws-1", attribution=_attribution(), outcome_measurement={"measured_success":True,"confidence":0.9}).to_dict()
    feedback["execution_authority"] = True
    with pytest.raises(ValueError, match="execute"):
        validate_learning_feedback(feedback)
