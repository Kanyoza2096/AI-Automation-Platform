import pytest
from core.tenant_repository import (
    WorkspaceRequiredError,
    require_workspace_id,
    assert_row_workspace,
)


def test_require_workspace_id():
    assert require_workspace_id("ws-1") == "ws-1"
    with pytest.raises(WorkspaceRequiredError):
        require_workspace_id(None)
    with pytest.raises(WorkspaceRequiredError):
        require_workspace_id("  ")


def test_assert_row_workspace():
    assert_row_workspace({"id": "1", "workspace_id": "a"}, "a")
    with pytest.raises(PermissionError):
        assert_row_workspace({"id": "1", "workspace_id": "b"}, "a")
