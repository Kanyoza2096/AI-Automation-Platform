from types import SimpleNamespace
import pytest
from services.business_outcome_measurement import (
    build_execution_observation, build_business_outcome_observation,
    validate_outcome_observation, observation_fingerprint,
)


def test_execution_receipt_never_claims_business_success():
    receipt = SimpleNamespace(workspace_id="w1", execution_id="e1", action="send_message",
                              status="succeeded", provider="x", provider_object_id="p1", evidence={})
    obs = build_execution_observation(receipt)
    assert obs["execution_status"] == "succeeded"
    assert obs["business_success"] is None
    assert obs["attribution_status"] == "not_yet_attributed"
    assert obs["authority"] == "none"


def test_uncertain_execution_stays_unknown():
    receipt = {"workspace_id":"w1", "execution_id":"e2", "action":"publish",
               "status":"uncertain", "evidence":{}}
    obs = build_execution_observation(receipt)
    assert obs["outcome_state"] == "unknown"
    assert obs["business_success"] is None


def test_business_outcome_can_be_attributed():
    obs = build_business_outcome_observation(
        workspace_id="w1", execution_id="e3", outcome_state="observed",
        business_success=True, confidence=.91, source="analytics", evidence={"metric":"conversion"},
        external_event_id="conv-1")
    assert obs["attribution_status"] == "attributed"
    assert obs["business_success_confidence"] == .91
    validate_outcome_observation(obs)


def test_unknown_cannot_claim_success():
    with pytest.raises(ValueError):
        build_business_outcome_observation(
            workspace_id="w1", execution_id="e4", outcome_state="unknown",
            business_success=True, confidence=.9, source="system")


def test_invalid_authority_fails_closed():
    obs = build_business_outcome_observation(
        workspace_id="w1", execution_id="e5", outcome_state="observed",
        business_success=None, confidence=None, source="customer")
    obs["authority"] = "execute"
    with pytest.raises(ValueError):
        validate_outcome_observation(obs)


def test_fingerprint_is_stable():
    obs = build_business_outcome_observation(
        workspace_id="w1", execution_id="e6", outcome_state="observed",
        business_success=False, confidence=.8, source="analytics")
    assert observation_fingerprint(obs) == observation_fingerprint(dict(obs))
