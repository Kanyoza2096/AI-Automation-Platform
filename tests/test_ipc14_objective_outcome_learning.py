import pytest
from services.ipc14_objective_outcome_learning import (
    build_objective_learning_signal,
    validate_objective_learning,
    verify_objective_outcome,
)


def ctx():
    return {"workspace_id":"ws1", "run_id":"run1", "objective_id":"obj1", "authority":"none",
            "success_criteria":{"qualified_conversations":{"operator":">=","target":5}}}

def attr():
    return {"workspace_id":"ws1", "execution_id":"exec1", "attribution_status":"attributed",
            "attribution_id":"attr1", "attribution_method":"exact_execution_id", "authority":"none"}

def obs(success=True, objective="obj1"):
    return {"observation_type":"business_outcome", "observation_id":"obs1", "workspace_id":"ws1",
            "execution_id":"exec1", "objective_id":objective, "business_success":success,
            "business_success_confidence":0.9, "metrics":{"qualified_conversations":7}, "source":"crm"}

def test_verified_objective_progress_becomes_advisory_learning():
    v = verify_objective_outcome(observation=obs(), execution_context=ctx(), attribution=attr())
    assert v["state"] == "objective_progress"
    s = build_objective_learning_signal(v)
    assert s["learning_state"] == "positive"
    assert s["advisory_only"] is True
    assert s["authority"] == "none"

def test_regression_is_learning_not_authority():
    v = verify_objective_outcome(observation=obs(False), execution_context=ctx(), attribution=attr())
    assert v["state"] == "objective_regression"
    assert build_objective_learning_signal(v)["learning_state"] == "negative"

def test_uncertain_when_criteria_fail_but_business_success_claim_is_not_false():
    o = obs(); o["metrics"] = {"qualified_conversations": 2}; o["business_success"] = None
    v = verify_objective_outcome(observation=o, execution_context=ctx(), attribution=attr())
    assert v["state"] == "objective_uncertain"

def test_cross_workspace_rejected():
    with pytest.raises(ValueError, match="workspace mismatch"):
        verify_objective_outcome(observation={**obs(), "workspace_id":"ws2"}, execution_context=ctx(), attribution=attr())

def test_objective_tampering_rejected():
    with pytest.raises(ValueError, match="objective outcome binding mismatch"):
        verify_objective_outcome(observation=obs(objective="obj2"), execution_context=ctx(), attribution=attr())

def test_unattributed_outcome_cannot_be_learning():
    with pytest.raises(ValueError, match="attributed outcome"):
        verify_objective_outcome(observation=obs(), execution_context=ctx(), attribution={**attr(), "attribution_status":"review_required"})

def test_learning_cannot_gain_policy_authority():
    v = verify_objective_outcome(observation=obs(), execution_context=ctx(), attribution=attr())
    s = build_objective_learning_signal(v)
    s["execution_effect"] = "allow"
    with pytest.raises(ValueError, match="learning cannot mutate"):
        validate_objective_learning(s)
