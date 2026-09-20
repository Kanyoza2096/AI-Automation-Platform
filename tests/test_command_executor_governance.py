from pathlib import Path
import asyncio
from types import SimpleNamespace

from services.command_executor import CommandExecutor


class ReadOnlyPlugin:
    def __init__(self):
        self.calls = []

    def get_commands(self):
        return [{"intent": "status", "execution_mode": "read_only"}]

    def execute(self, command, params, workspace_id):
        self.calls.append((command, params, workspace_id))
        return {"ok": True, "status": "ready"}


class UngovernedPlugin:
    def get_commands(self):
        return [{"intent": "delete_everything"}]

    def execute(self, command, params, workspace_id):
        raise AssertionError("ungoverned plugin must never execute")


def _executor(plugin):
    c = SimpleNamespace(industry_plugins={"demo": plugin})
    e = CommandExecutor(SimpleNamespace(), container=c)
    return e


def test_read_only_plugin_command_may_execute_without_becoming_mutation_plane(monkeypatch):
    plugin = ReadOnlyPlugin()
    monkeypatch.setattr("core.plugin_execution.resolve_plugin_workspace", lambda requested: "ws-1")
    result = _executor(plugin)._execute_plugin({"plugin": "demo", "command": "status", "params": {}})
    assert result["ok"] is True
    assert plugin.calls == [("status", {}, "ws-1")]


def test_ungoverned_plugin_mutation_is_fenced_before_plugin_execution(monkeypatch):
    plugin = UngovernedPlugin()
    monkeypatch.setattr("core.plugin_execution.resolve_plugin_workspace", lambda requested: "ws-1")
    result = _executor(plugin)._execute_plugin({"plugin": "demo", "command": "delete_everything", "params": {}})
    assert result["ok"] is False
    assert result["status"] == "governance_required"


def test_governed_plugin_contract_is_explicit_in_command_executor():
    source = Path(__file__).resolve().parents[1] / "services" / "command_executor.py"
    text = source.read_text(encoding="utf-8")
    assert "execution_mode != \"action_kernel\"" in text
    assert "governed_action_type" in text
    assert "build_default_action_kernel" in text
