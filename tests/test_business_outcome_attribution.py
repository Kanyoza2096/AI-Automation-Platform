import pytest
from services.business_outcome_attribution import (
    attribute_business_outcome, measure_outcomes, validate_attribution,
)


def outcome(**overrides):
    value = {
        "schema_version": "1.0", "observation_id": "obs-1", "workspace_id": "w1",
        "execution_id": "e1", "observation_type": "business_outcome",
        "outcome_state": "observed", "business_success": True,
        "business_success_confidence": .9, "source": "analytics", "authority": "none",
    }
    value.update(overrides)
    return value


def candidate(**overrides):
    value = {"workspace_id": "w1", "execution_id": "e1", "action": "send_message",
             "decision_id": "d1", "evaluation_id": "ev1", "candidate_id": "c1", "handoff_id": "h1"}
    value.update(overrides)
    return value


def test_exact_execution_match_is_preferred():
    result = attribute_business_outcome(outcome(), [candidate(), candidate(execution_id="e2")])
    assert result["attribution_status"] == "attributed"
    assert result["attribution_method"] == "exact_execution_id"
    assert result["decision_id"] == "d1"


def test_ambiguous_external_mapping_requires_review():
    obs = outcome(execution_id="unknown", observation_id="obs-2")
    result = attribute_business_outcome(obs, [candidate(execution_id="e2", external_event_ids=("conv-1",)),
                                              candidate(execution_id="e3", external_event_ids=("conv-1",))],
                                        external_event_id="conv-1")
    assert result["attribution_status"] == "review_required"
    assert result["attribution_method"] == "ambiguous"
    assert result["execution_id"] is None


def test_no_match_is_not_guessed_by_time_or_action():
    obs = outcome(execution_id="unknown", observation_id="obs-3")
    result = attribute_business_outcome(obs, [candidate(execution_id="e9")])
    assert result["attribution_status"] == "unattributed"
    assert result["execution_id"] is None


def test_external_event_id_can_attribute_exactly_one():
    obs = outcome(execution_id="unknown", observation_id="obs-4")
    result = attribute_business_outcome(obs, [candidate(execution_id="e9", external_event_ids=("conv-9",))],
                                        external_event_id="conv-9")
    assert result["attribution_status"] == "attributed"
    assert result["execution_id"] == "e9"
    assert result["attribution_method"] == "external_event_id"


def test_measurement_separates_execution_and_business_success():
    observations = [
        {"observation_type":"execution_receipt", "execution_status":"succeeded", "outcome_state":"succeeded"},
        {"observation_type":"execution_receipt", "execution_status":"failed", "outcome_state":"failed"},
        outcome(observation_id="obs-5", execution_id="e1", business_success=True, business_success_confidence=.8),
        outcome(observation_id="obs-6", execution_id="e2", business_success=False, business_success_confidence=.6),
    ]
    attrs = [attribute_business_outcome(observations[2], [candidate()]),
             attribute_business_outcome(observations[3], [candidate(execution_id="e2")])]
    metrics = measure_outcomes(observations, attrs)
    assert metrics["execution_success_rate"] == .5
    assert metrics["business_success_rate"] == .5
    assert metrics["attributed_conversion_rate"] == .5
    assert metrics["authority"] == "none"


def test_authority_tampering_fails_closed():
    result = attribute_business_outcome(outcome(), [candidate()])
    result["authority"] = "execute"
    with pytest.raises(ValueError):
        validate_attribution(result)
