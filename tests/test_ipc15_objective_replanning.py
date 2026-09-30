import pytest
from services.ipc15_objective_replanning import (
    admit_objective_lifecycle_transition,
    recommend_replan,
    validate_replan_recommendation,
)


def verification(state="objective_progress"):
    return {"verification_id":"ov1", "workspace_id":"ws1", "objective_id":"obj1", "state":state, "authority":"none"}

def objective(state="active"):
    return {"workspace_id":"ws1", "objective_id":"obj1", "state":state, "version":3}

def test_progress_continues_without_mutating_objective():
    r = recommend_replan(verification=verification(), objective=objective())
    assert r["disposition"] == "continue"
    assert r["proposed_state"] == "active"
    assert r["next_cycle"] is True
    assert r["authority"] == "none"


def test_regression_requests_adaptation_not_policy_mutation():
    r = recommend_replan(verification=verification("objective_regression"), objective=objective())
    assert r["disposition"] == "adapt"
    assert r["proposed_state"] == "active"
    assert r["policy_effect"] == "none"


def test_uncertain_escalates_and_proposes_pause():
    r = recommend_replan(verification=verification("objective_uncertain"), objective=objective())
    assert r["disposition"] == "escalate"
    assert r["proposed_state"] == "paused"


def test_cross_workspace_rejected():
    with pytest.raises(ValueError, match="workspace mismatch"):
        recommend_replan(verification=verification(), objective={**objective(), "workspace_id":"ws2"})


def test_tampered_replan_cannot_be_admitted():
    r = recommend_replan(verification=verification("objective_regression"), objective=objective())
    r["proposed_state"] = "completed"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        validate_replan_recommendation(r)


def test_lifecycle_change_requires_separate_governance_decision():
    r = recommend_replan(verification=verification("objective_uncertain"), objective=objective())
    with pytest.raises(ValueError, match="governed lifecycle decision"):
        admit_objective_lifecycle_transition(objective=objective(), recommendation=r, governed_decision={"authority":"none", "decision":"allow"})


def test_governed_transition_is_admitted_without_becoming_execution():
    r = recommend_replan(verification=verification("objective_uncertain"), objective=objective())
    t = admit_objective_lifecycle_transition(
        objective=objective(), recommendation=r,
        governed_decision={"decision_id":"d1", "workspace_id":"ws1", "objective_id":"obj1", "authority":"human_approval", "decision":"approve"},
    )
    assert t["to_state"] == "paused"
    assert t["execution_effect"] == "none"
