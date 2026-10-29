import pytest
from services.v83_identity_access_hardening import IdentityAccessHardening, IdentityAccessViolation


def test_identity_and_session_are_durable_and_scope_bound(tmp_path):
    db = tmp_path / "identity.db"
    p = IdentityAccessHardening(str(db))
    p.register_identity("u1", "t1", "ws1")
    p.open_session("s1", "u1", "t1", "ws1", "2030-01-01T00:00:00+00:00", issued_at="2029-01-01T00:00:00+00:00")
    p.close()
    p = IdentityAccessHardening(str(db))
    assert p.validate_session("s1", "t1", "ws1", now="2029-01-02T00:00:00+00:00").principal_id == "u1"


def test_scope_and_identity_state_fail_closed():
    p = IdentityAccessHardening()
    p.register_identity("u1", "t1", "ws1")
    p.open_session("s1", "u1", "t1", "ws1", "2030-01-01T00:00:00+00:00", issued_at="2029-01-01T00:00:00+00:00")
    with pytest.raises(IdentityAccessViolation, match="session_tenant_mismatch"): p.validate_session("s1", "t2", "ws1")
    with pytest.raises(IdentityAccessViolation, match="session_workspace_mismatch"): p.validate_session("s1", "t1", "ws2")
    with pytest.raises(IdentityAccessViolation, match="identity_scope_rebinding_denied"): p.register_identity("u1", "t2", "ws2")


def test_expiry_and_revocation_are_enforced():
    p = IdentityAccessHardening()
    p.register_identity("u1", "t1", "ws1")
    p.open_session("s1", "u1", "t1", "ws1", "2029-01-02T00:00:00+00:00", issued_at="2029-01-01T00:00:00+00:00")
    with pytest.raises(IdentityAccessViolation, match="session_expired"): p.validate_session("s1", "t1", "ws1", now="2029-01-03T00:00:00+00:00")
    p.open_session("s2", "u1", "t1", "ws1", "2030-01-02T00:00:00+00:00", issued_at="2030-01-01T00:00:00+00:00")
    p.revoke_session("s2")
    with pytest.raises(IdentityAccessViolation, match="session_revoked"): p.validate_session("s2", "t1", "ws1", now="2030-01-01T01:00:00+00:00")


def test_tampering_is_detected():
    p = IdentityAccessHardening()
    p.register_identity("u1", "t1", "ws1")
    p._db.execute("UPDATE identities SET tenant_id='t2' WHERE principal_id='u1'"); p._db.commit()
    assert not p.tamper_check_identity("u1")
    with pytest.raises(IdentityAccessViolation, match="identity_unproven"): p.open_session("s1", "u1", "t1", "ws1", "2030-01-01T00:00:00+00:00")


def test_identity_and_session_layers_have_no_authority():
    from services.v83_identity_access_hardening import PrincipalIdentity, AccessSession
    with pytest.raises(ValueError, match="authority"):
        PrincipalIdentity("u","t","w",__import__('services.v83_identity_access_hardening', fromlist=['IdentityState']).IdentityState.ACTIVE,"standard","0"*64,"0"*64,"v8.3","now","0"*64,can_execute=True)
    with pytest.raises(ValueError, match="authority"):
        AccessSession("s","u","t","w",__import__('services.v83_identity_access_hardening', fromlist=['SessionState']).SessionState.ACTIVE,"standard","now","later","now","0"*64,can_execute=True)
