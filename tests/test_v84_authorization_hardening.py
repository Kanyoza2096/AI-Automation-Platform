from datetime import datetime, timezone, timedelta
import tempfile
from services.v83_identity_access_hardening import IdentityAccessHardening
from services.v84_authorization_hardening import CanonicalAuthorizationHardening, AuthorizationHardeningViolation


def _expires():
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()


def _setup(tmp):
    ia = IdentityAccessHardening(tmp + "/identity.db")
    ia.register_identity("p1", "t1", "w1")
    ia.open_session("s1", "p1", "t1", "w1", _expires())
    ah = CanonicalAuthorizationHardening(ia, tmp + "/auth.db")
    return ia, ah


def test_canonical_authorization_is_scoped_and_durable():
    with tempfile.TemporaryDirectory() as tmp:
        ia, ah = _setup(tmp)
        d = ah.authorize(session_id="s1", tenant_id="t1", workspace_id="w1", action_type="read_status", payload={})
        assert d.workspace_id == "w1"
        assert ah.get(d.decision_id, tenant_id="t1", workspace_id="w1").decision_id == d.decision_id
        ia.close(); ah.close()
        ia2 = IdentityAccessHardening(tmp + "/identity.db")
        ah2 = CanonicalAuthorizationHardening(ia2, tmp + "/auth.db")
        assert ah2.get(d.decision_id, tenant_id="t1", workspace_id="w1").allowed is not None


def test_cross_workspace_and_tenant_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        ia, ah = _setup(tmp)
        try:
            ah.authorize(session_id="s1", tenant_id="t1", workspace_id="w2", action_type="read_status", payload={})
            assert False
        except AuthorizationHardeningViolation:
            pass
        try:
            ah.get("missing", tenant_id="t2", workspace_id="w1")
            assert False
        except AuthorizationHardeningViolation:
            pass


def test_expired_or_revoked_session_cannot_authorize():
    with tempfile.TemporaryDirectory() as tmp:
        ia, ah = _setup(tmp)
        ia.revoke_session("s1")
        try:
            ah.authorize(session_id="s1", tenant_id="t1", workspace_id="w1", action_type="read_status", payload={})
            assert False
        except AuthorizationHardeningViolation:
            # Identity layer may raise its own PermissionError subtype.
            pass


def test_tampered_decision_is_not_trusted():
    with tempfile.TemporaryDirectory() as tmp:
        ia, ah = _setup(tmp)
        d = ah.authorize(session_id="s1", tenant_id="t1", workspace_id="w1", action_type="read_status", payload={})
        ah._db.execute("UPDATE authorization_decisions SET reason='tampered' WHERE decision_id=?", (d.decision_id,)); ah._db.commit()
        try:
            ah.get(d.decision_id, tenant_id="t1", workspace_id="w1")
            assert False
        except AuthorizationHardeningViolation as e:
            assert "tampered" in str(e)


def test_authorization_layer_has_no_execution_authority():
    with tempfile.TemporaryDirectory() as tmp:
        ia, ah = _setup(tmp)
        d = ah.authorize(session_id="s1", tenant_id="t1", workspace_id="w1", action_type="read_status", payload={})
        assert d.pipeline.get("allowed") in (True, False)
        assert "provider" not in d.pipeline or d.pipeline.get("provider") is None
