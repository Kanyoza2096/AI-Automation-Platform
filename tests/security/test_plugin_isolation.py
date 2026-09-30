"""Plugin execution must not trust client workspace for tenants."""

from core.request_context import RequestContext, clear_request_context, set_request_context
from core.plugin_execution import run_plugin


def test_run_plugin_strips_client_workspace():
    set_request_context(
        RequestContext(
            principal_id="u",
            workspace_id="safe-ws",
            species_privileges=False,
        )
    )
    seen = {}

    def fake_execute(command, params, workspace_id="default"):
        seen["command"] = command
        seen["params"] = params
        seen["workspace_id"] = workspace_id
        return {"ok": True}

    result = run_plugin(
        fake_execute,
        "add_contact",
        {"workspace_id": "evil", "name": "Ada"},
        client_workspace_id="evil",
    )
    assert result["ok"] is True
    assert seen["workspace_id"] == "safe-ws"
    assert "workspace_id" not in seen["params"]
    clear_request_context()
