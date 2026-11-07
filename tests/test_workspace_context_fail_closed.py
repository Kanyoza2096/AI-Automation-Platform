import pytest

from config.settings import settings
from core.workspace_context import (
    clear_workspace_context,
    get_workspace_context,
    require_workspace_context,
)


def test_unbound_workspace_context_does_not_use_environment_default(monkeypatch):
    clear_workspace_context()
    monkeypatch.setattr(settings, "default_workspace_id", "production-workspace")
    with pytest.raises(PermissionError, match="No trusted WorkspaceContext"):
        get_workspace_context()


def test_explicit_legacy_default_requires_opt_in(monkeypatch):
    clear_workspace_context()
    monkeypatch.setattr(settings, "default_workspace_id", "explicit-legacy-workspace")
    context = require_workspace_context(allow_default=True)
    assert context.workspace_id == "explicit-legacy-workspace"
    clear_workspace_context()
