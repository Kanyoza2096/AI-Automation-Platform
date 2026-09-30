import pytest
from services.autonomous_exception_recovery import AutonomousExceptionRecovery
from services.ipc17_exception_recovery import (
    ObjectiveRecoveryContext,
    bind_recovery_to_objective,
    evaluate_objective_recovery,
    validate_recovery_resume,
)


def obs(**meta):
    return AutonomousExceptionRecovery().observe(
        workspace_id="ws1", execution_id="ex1", action_type="crm_followup",
        observed_status="failed", error="provider timeout", error_code="timeout",
        intent_id="intent1", metadata={"objective_id": "obj1", **meta},
    )


def ctx():
    return ObjectiveRecoveryContext("ws1", "obj1", "cycle1", "run1")


def test_objective_recovery_preserves_lineage():
    o = obs(cycle_id="cycle1", run_id="run1")
    p, lineage = evaluate_objective_recovery(context=ctx(), observation=o, idempotent=True)
    assert p.authority == "none"
    assert lineage["objective_id"] == "obj1"
    assert lineage["cycle_id"] == "cycle1"
    assert lineage["authority"] == "none"


def test_cross_workspace_recovery_rejected():
    o = obs(cycle_id="cycle1")
    c = ObjectiveRecoveryContext("ws2", "obj1", "cycle1")
    with pytest.raises(ValueError, match="workspace"):
        evaluate_objective_recovery(context=c, observation=o, idempotent=True)


def test_cross_objective_recovery_rejected():
    o = obs(cycle_id="cycle1")
    c = ObjectiveRecoveryContext("ws1", "obj2", "cycle1")
    with pytest.raises(ValueError, match="objective"):
        evaluate_objective_recovery(context=c, observation=o, idempotent=True)


def test_cycle_mismatch_rejected():
    o = obs(cycle_id="cycle-other")
    with pytest.raises(ValueError, match="cycle"):
        evaluate_objective_recovery(context=ctx(), observation=o, idempotent=True)


def test_missing_objective_evidence_fails_closed():
    o = AutonomousExceptionRecovery().observe(
        workspace_id="ws1", execution_id="ex1", action_type="crm_followup",
        observed_status="failed", error="timeout", error_code="timeout", metadata={}
    )
    with pytest.raises(ValueError, match="objective evidence"):
        evaluate_objective_recovery(context=ctx(), observation=o, idempotent=True)


def test_tampered_stored_lineage_rejected():
    with pytest.raises(ValueError, match="objective"):
        validate_recovery_resume(context=ctx(), stored_lineage={"workspace_id":"ws1","objective_id":"obj2","cycle_id":"cycle1","authority":"none"}, workspace_id="ws1", objective_id="obj1")


def test_recovery_never_grants_authority():
    o = obs(cycle_id="cycle1")
    p = AutonomousExceptionRecovery().evaluate(o, idempotent=True)
    with pytest.raises(ValueError, match="authority"):
        bind_recovery_to_objective(observation=o, plan=p, context=ObjectiveRecoveryContext("ws1", "obj1", "cycle1", authority="policy"))


def test_resume_requires_same_workspace_and_objective():
    validate_recovery_resume(
        context=ctx(),
        stored_lineage={"workspace_id":"ws1","objective_id":"obj1","cycle_id":"cycle1","authority":"none"},
        workspace_id="ws1", objective_id="obj1"
    )
