import pytest
from services.v82_tenant_workspace_isolation import TenantWorkspaceIsolation, IsolationViolation


def test_scope_is_durable_and_exact():
    import tempfile, pathlib
    db = pathlib.Path(tempfile.mkdtemp()) / "iso.db"
    p = TenantWorkspaceIsolation(str(db)); p.bind("worker", "w1", "t1", "ws1"); p.close()
    p = TenantWorkspaceIsolation(str(db))
    assert p.assert_access("worker", "w1", "t1", "ws1").tenant_id == "t1"
    assert [x.resource_id for x in p.list_scope("t1", "ws1")] == ["w1"]


def test_cross_tenant_and_workspace_access_fail_closed():
    p = TenantWorkspaceIsolation(); p.bind("worker", "w1", "t1", "ws1")
    with pytest.raises(IsolationViolation, match="tenant_mismatch"): p.assert_access("worker", "w1", "t2", "ws1")
    with pytest.raises(IsolationViolation, match="workspace_mismatch"): p.assert_access("worker", "w1", "t1", "ws2")


def test_unknown_resource_and_rebinding_are_denied():
    p = TenantWorkspaceIsolation(); p.bind("worker", "w1", "t1", "ws1")
    with pytest.raises(IsolationViolation, match="resource_scope_unknown"): p.assert_access("worker", "missing", "t1", "ws1")
    with pytest.raises(IsolationViolation, match="resource_scope_rebinding_denied"): p.bind("worker", "w1", "t2", "ws2")


def test_tampering_is_detected():
    p = TenantWorkspaceIsolation(); p.bind("worker", "w1", "t1", "ws1")
    p._db.execute("UPDATE isolation_bindings SET tenant_id='t2' WHERE resource_type='worker' AND resource_id='w1'"); p._db.commit()
    with pytest.raises(IsolationViolation, match="isolation_record_tampered"): p.assert_access("worker", "w1", "t1", "ws1")
    assert p.tamper_check("worker", "w1") is False


def test_isolation_boundary_has_no_authority():
    from services.v82_tenant_workspace_isolation import IsolationBinding
    with pytest.raises(ValueError, match="authority"):
        IsolationBinding("x", "1", "t", "w", "now", "0" * 64, can_execute=True)
