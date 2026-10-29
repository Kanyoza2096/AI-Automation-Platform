from services.v81_security_control_plane import (
    ControlState, PostureState, SecurityControl, SecurityControlPlane, evidence_fingerprint,
)


def control(ws="w1", cid="identity"):
    return SecurityControl(cid, ws, cid.replace("_", " "), ControlState.ENABLED, "1", evidence_fingerprint(ws, cid, "ok"), "2026-10-05T00:00:00+00:00")


def test_healthy_posture_is_durable_and_tenant_scoped(tmp_path):
    db = tmp_path / "security.db"
    p = SecurityControlPlane(str(db))
    p.upsert_control(control())
    posture = p.evaluate("w1")
    assert posture.state == PostureState.HEALTHY
    p.close()
    p2 = SecurityControlPlane(str(db))
    assert p2.get_control("w1", "identity") is not None
    assert p2.get_control("w2", "identity") is None
    assert p2.tamper_check("w1", "identity") is True


def test_degraded_and_quarantine_fail_closed():
    p = SecurityControlPlane()
    p.upsert_control(control(cid="identity"))
    p.upsert_control(SecurityControl("network", "w1", "network", ControlState.DEGRADED, "1", evidence_fingerprint("d"), "2026-10-05T00:00:00+00:00"))
    assert p.evaluate("w1").state == PostureState.DEGRADED
    p.upsert_control(SecurityControl("containment", "w1", "containment", ControlState.QUARANTINED, "1", evidence_fingerprint("q"), "2026-10-05T00:00:00+00:00"))
    assert p.evaluate("w1").state == PostureState.SECURITY_HOLD


def test_unknown_without_controls_is_not_healthy():
    assert SecurityControlPlane().evaluate("w1").state == PostureState.UNKNOWN


def test_authority_boundary_is_hard():
    try:
        SecurityControl("x", "w1", "x", ControlState.ENABLED, "1", "0" * 64, "now", can_execute=True)
    except ValueError as exc:
        assert "authority" in str(exc)
    else:
        raise AssertionError("control must not grant execution authority")


def test_tamper_is_detected():
    p = SecurityControlPlane()
    p.upsert_control(control())
    p._db.execute("UPDATE security_controls SET state='degraded' WHERE workspace_id='w1' AND control_id='identity'")
    p._db.commit()
    assert p.tamper_check("w1", "identity") is False
