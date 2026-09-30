from __future__ import annotations
import pytest
from core.request_context import RequestContext, set_request_context, clear_request_context, get_request_context
from core.plugin_execution import resolve_plugin_workspace

@pytest.fixture(autouse=True)
def _clear():
    clear_request_context(); yield; clear_request_context()

def test_user_a_cannot_use_workspace_b_via_body():
    set_request_context(RequestContext(principal_id="user-a", workspace_id="ws-a", role="editor", permissions=("read","write")))
    assert resolve_plugin_workspace("ws-b") == "ws-a"

def test_user_a_can_use_own_workspace():
    set_request_context(RequestContext(principal_id="user-a", workspace_id="ws-a", role="editor", permissions=("read","write")))
    assert resolve_plugin_workspace("ws-a") == "ws-a"

def test_species_operator_may_target_workspace():
    set_request_context(RequestContext(principal_id="ops", workspace_id="ws-a", role="species", permissions=("read","write","admin"), species_privileges=True))
    assert resolve_plugin_workspace("ws-b") == "ws-b"

def test_missing_context_fails_closed():
    clear_request_context()
    assert get_request_context() is None
    with pytest.raises(Exception):
        resolve_plugin_workspace("ws-x")
