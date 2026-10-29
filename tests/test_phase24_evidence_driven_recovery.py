from services.director import Director
from services.director.models import Authority, Risk, Sector, Status, Task


def _task(task_id, status, result=None, depends_on=None):
    return Task(
        id=task_id, sector=Sector.SELF, description=task_id,
        capability="self_diagnostic", authority=Authority.SUGGEST,
        risk=Risk.LOW, status=status, result=result,
        depends_on=list(depends_on or []),
    )


def test_unknown_outcome_requires_reconciliation_even_if_handler_acknowledged():
    director = Director(supabase_client=None)
    director.tasks["t1"] = _task("t1", Status.FLAGGED, {
        "ok": True, "status": "executed",
        "outcome_verification": {"outcome": "unknown", "reason": "observer_unavailable"},
    })
    result = director.assess_task_recovery("t1")
    assert result["retry_safety"] == "unsafe_until_reconciled"
    assert result["recovery_evidence"] == {
        "outcome_verification": "unknown",
        "handler_acknowledged": True,
        "handler_acknowledgement_is_success_proof": False,
        "reconciliation_required": True,
    }
    assert result["automatic_retry_allowed"] is False
    assert any("authoritative domain state" in step for step in result["suggested_next_steps"])


def test_failed_task_without_receipt_is_not_assumed_to_have_no_side_effect():
    director = Director(supabase_client=None)
    director.tasks["t1"] = _task("t1", Status.FAILED, result=None)
    result = director.assess_task_recovery("t1")
    assert result["recovery_evidence"]["outcome_verification"] == "not_recorded"
    assert result["retry_safety"] == "unsafe_until_reconciled"
    assert result["recovery_evidence"]["reconciliation_required"] is True


def test_blocked_task_identifies_dependency_block_without_retrying():
    director = Director(supabase_client=None)
    director.tasks["failed"] = _task("failed", Status.INEFFECTIVE)
    director.tasks["blocked"] = _task("blocked", Status.BLOCKED, depends_on=["failed", "missing"])
    result = director.assess_task_recovery("blocked")
    assert result["retry_safety"] == "blocked_by_dependencies"
    assert result["recovery_evidence"]["reconciliation_required"] is True
    assert result["automatic_action_taken"] is False
    assert result["automatic_retry_allowed"] is False


def test_verified_task_has_no_recovery_or_reconciliation_requirement():
    director = Director(supabase_client=None)
    director.tasks["done"] = _task("done", Status.VERIFIED, {
        "ok": True, "outcome_verification": {"outcome": "verified"}
    })
    result = director.assess_task_recovery("done")
    assert result["retry_safety"] == "not_applicable_completed"
    assert result["recovery_evidence"]["reconciliation_required"] is False
    assert result["requires_human_review"] is False
