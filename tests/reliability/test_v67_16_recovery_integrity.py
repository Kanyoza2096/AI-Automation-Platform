from services.v67_recovery_integrity import audit_recovery_integrity, validate_recovery_integrity_report


def base():
    return ([{"plan_id":"p1","exception_id":"e1","workspace_id":"w1","action_type":"crm_schedule_followup","disposition":"auto_recover","max_attempts":3,"state":"recovered","authority":"none"}],
            [{"exception_id":"e1","workspace_id":"w1","intent_id":"i1","state":"recovered"}],
            [{"intent_id":"i1","workspace_id":"w1","action_type":"crm_schedule_followup","recovery_plan_id":"p1","status":"succeeded"}])


def test_healthy_recovery_lineage():
    r = audit_recovery_integrity(*base())
    assert r.ok and not r.issues and r.authority == "none"
    validate_recovery_integrity_report(r)


def test_recovered_without_success_is_false_recovery():
    p, e, o = base(); o[0]["status"] = "failed"
    r = audit_recovery_integrity(p, e, o)
    assert any(i.code == "FALSE_RECOVERY" for i in r.issues)


def test_released_terminal_outcome_is_stale_control_state():
    p, e, o = base(); p[0]["state"] = "released"; e[0]["state"] = "recovery_planned"
    r = audit_recovery_integrity(p, e, o)
    assert any(i.code == "STALE_RELEASE_STATE" for i in r.issues)


def test_plan_workspace_mismatch_fails_closed():
    p, e, o = base(); p[0]["workspace_id"] = "w2"
    r = audit_recovery_integrity(p, e, o)
    assert any(i.code == "WORKSPACE_LINEAGE_MISMATCH" for i in r.issues)


def test_outbox_plan_mismatch_is_detected():
    p, e, o = base(); o[0]["recovery_plan_id"] = "other"
    r = audit_recovery_integrity(p, e, o)
    assert any(i.code == "RECOVERY_PLAN_LINEAGE_MISMATCH" for i in r.issues)


def test_recovered_exception_requires_recovered_plan():
    p, e, o = base(); p[0]["state"] = "released"
    r = audit_recovery_integrity(p, e, o)
    assert any(i.code == "EXCEPTION_PLAN_DISAGREEMENT" for i in r.issues)
