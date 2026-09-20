from services.v67_recovery_lifecycle import certify_recovery_success, recovery_success_rpc_payload


def test_only_exact_success_closes_recovery():
    d = certify_recovery_success(
        plan_id="rec1", intent_id="act1", workspace_id="ws1",
        outbox_status="succeeded", recovery_plan_id="rec1", execution_id="ex1",
    )
    assert d.allowed and d.state == "recovered" and d.authority == "none"


def test_failed_or_uncertain_cannot_close_recovery():
    for status in ("failed", "uncertain", "reconciliation_required", "processing"):
        d = certify_recovery_success(
            plan_id="rec1", intent_id="act1", workspace_id="ws1",
            outbox_status=status, recovery_plan_id="rec1",
        )
        assert not d.allowed


def test_plan_lineage_mismatch_fails_closed():
    d = certify_recovery_success(
        plan_id="rec1", intent_id="act1", workspace_id="ws1",
        outbox_status="succeeded", recovery_plan_id="rec2",
    )
    assert not d.allowed


def test_identity_is_required():
    d = certify_recovery_success(
        plan_id="", intent_id="act1", workspace_id="ws1",
        outbox_status="succeeded", recovery_plan_id="rec1",
    )
    assert not d.allowed


def test_rpc_payload_is_narrow_and_authority_free():
    payload = recovery_success_rpc_payload(plan_id="rec1", intent_id="act1", workspace_id="ws1", execution_id="ex1")
    assert payload == {"p_plan_id": "rec1", "p_intent_id": "act1", "p_workspace_id": "ws1", "p_execution_id": "ex1"}
    assert "approved" not in payload
    assert "provider" not in payload
