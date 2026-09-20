import asyncio

from fastapi import HTTPException

from core.request_context import RequestContext, clear_request_context, set_request_context
from core.security.deps import require_species_privileges


def _request(ctx):
    return type("Req", (), {"state": type("State", (), {"request_context": ctx})()})()


def test_global_surface_rejects_workspace_admin():
    ctx = RequestContext(principal_id="u", workspace_id="ws-a", role="admin", permissions=("admin",))
    set_request_context(ctx)
    try:
        try:
            asyncio.run(require_species_privileges(_request(ctx)))
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("workspace admin reached global control-plane surface")
    finally:
        clear_request_context()


def test_global_surface_accepts_species():
    ctx = RequestContext(principal_id="species", workspace_id="platform", role="species", species_privileges=True)
    set_request_context(ctx)
    try:
        assert asyncio.run(require_species_privileges(_request(ctx))) is ctx
    finally:
        clear_request_context()
