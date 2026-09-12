from fastapi import HTTPException

from core.request_context import RequestContext, clear_request_context, set_request_context
from core.security.deps import require_species_privileges


def test_control_plane_rejects_ordinary_principal():
    set_request_context(RequestContext(principal_id="u", workspace_id="ws-a", role="admin", permissions=("admin",)))
    try:
        import asyncio
        try:
            asyncio.run(require_species_privileges(type("Req", (), {"state": type("State", (), {"request_context": None})()})()))
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("ordinary admin principal reached control plane")
    finally:
        clear_request_context()


def test_control_plane_accepts_species_principal():
    ctx = RequestContext(principal_id="species", workspace_id="platform", role="species", species_privileges=True)
    set_request_context(ctx)
    try:
        import asyncio
        result = asyncio.run(require_species_privileges(type("Req", (), {"state": type("State", (), {"request_context": ctx})()})()))
        assert result is ctx
    finally:
        clear_request_context()


def test_owner_role_inherits_operator_and_admin_authority():
    from core.authorization import at_least

    assert at_least("owner", "operator") is True
    assert at_least("owner", "admin") is True
    assert at_least("operator", "owner") is False
    assert at_least("admin", "owner") is False
