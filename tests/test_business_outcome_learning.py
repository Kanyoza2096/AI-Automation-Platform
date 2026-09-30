import pytest
from services.business_outcome_learning import (
    build_learning_signal, build_learning_batch, validate_learning_signal,
)


def outcome(**overrides):
    value = {
        "schema_version": "1.0", "observation_id": "obs-1", "workspace_id": "w1",
        "execution_id": "e1", "observation_type": "business_outcome",
        "outcome_state": "observed", "business_success": True,
        "business_success_confidence": .9, "source": "analytics",
    }
    value.update(overrides)
    return value


def attribution(**overrides):
    value = {
        "attribution_id": "attr-1", "workspace_id": "w1", "observation_id": "obs-1",
        "execution_id": "e1", "attribution_status": "attributed",
        "attribution_method": "exact_execution_id", "confidence": 1.0,
    }
    value.update(overrides)
    return value


def test_successful_attributed_outcome_creates_positive_learning_signal():
    result = build_learning_signal(outcome(), attribution())
    assert result["learning_state"] == "positive"
    assert result["target_type"] == "action"
    assert result["target_id"] == "e1"
    assert result["authority"] == "none"


def test_failed_business_outcome_creates_negative_learning_signal():
    result = build_learning_signal(
        outcome(business_success=False, business_success_confidence=.95), attribution())
    assert result["learning_state"] == "negative"


def test_low_confidence_never_becomes_positive_learning():
    result = build_learning_signal(
        outcome(business_success=True, business_success_confidence=.49), attribution())
    assert result["learning_state"] == "uncertain"


def test_unattributed_outcome_is_rejected_from_action_learning():
    with pytest.raises(ValueError):
        build_learning_signal(
            outcome(execution_id="unknown"),
            attribution(attribution_status="review_required", execution_id=None),
        )


def test_workspace_mismatch_fails_closed():
    with pytest.raises(ValueError):
        build_learning_signal(outcome(), attribution(workspace_id="w2"))


def test_authority_tampering_fails_closed():
    result = build_learning_signal(outcome(), attribution())
    result["authority"] = "execute"
    with pytest.raises(ValueError):
        validate_learning_signal(result)


def test_learning_signal_is_deterministic_for_same_evidence():
    first = build_learning_signal(outcome(), attribution())
    second = build_learning_signal(outcome(), attribution())
    assert first["signal_id"] == second["signal_id"]


def test_batch_skips_unattributed_record_without_poisoning_batch():
    observations = [
        outcome(observation_id="obs-1"),
        outcome(observation_id="obs-2", execution_id="unknown"),
    ]
    attrs = {
        "obs-1": attribution(observation_id="obs-1"),
        "obs-2": attribution(observation_id="obs-2", attribution_status="review_required", execution_id=None),
    }
    result = build_learning_batch(observations, attrs)
    assert len(result) == 1
    assert result[0]["observation_id"] == "obs-1"
