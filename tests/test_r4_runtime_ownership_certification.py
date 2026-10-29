from pathlib import Path

from services.command_executor import CommandExecutor
from services.runtime_ownership_certification import certify


def test_builtin_mutations_are_fenced_from_legacy_executor():
    executor = CommandExecutor(object(), base_url="http://unused")
    result = executor.execute({"action": "pause_scheduler", "params": {}})
    assert result["ok"] is False
    assert result["status"] == "governance_required"
    assert result["execution_authority"] == "action_kernel"
    assert result["legacy_executor"] == "non_authoritative"


def test_runtime_ownership_certification_passes():
    root = Path(__file__).resolve().parents[1]
    report = certify(root)
    assert report["status"] == "PASS", report
    assert all(item["status"] == "PASS" for item in report["findings"])
