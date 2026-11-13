from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "plugins/github_connector/routes/contracts.py"
ADMIN_PATH = ROOT / "plugins/github_connector/routes/admin.py"

_spec = importlib.util.spec_from_file_location("github_connector_contracts", CONTRACT_PATH)
_contracts = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _contracts
_spec.loader.exec_module(_contracts)
build_ci_payload = _contracts.build_ci_payload


def test_ci_payload_projects_github_workflow_runs():
    payload = build_ci_payload(
        [{"full_name": "org/repo"}],
        [{
            "total_count": 1,
            "workflow_runs": [{
                "id": 17,
                "name": "Control Plane CI",
                "status": "completed",
                "conclusion": "success",
                "head_branch": "KDO",
                "event": "push",
                "run_number": 42,
                "created_at": "2026-11-13T08:00:00Z",
                "updated_at": "2026-11-13T08:01:00Z",
                "html_url": "https://github.com/org/repo/actions/runs/17",
                "workflow_id": 9,
            }],
        }],
    )

    assert payload["ok"] is True
    assert payload["mock"] is False
    assert payload["source"] == "github_api"
    assert payload["count"] == 1
    assert payload["repos_scanned"] == 1
    assert payload["runs"][0]["repo"] == "org/repo"
    assert payload["runs"][0]["status"] == "completed"
    assert payload["runs"][0]["conclusion"] == "success"


def test_ci_payload_preserves_partial_fetch_errors():
    payload = build_ci_payload(
        [{"full_name": "org/a"}, {"full_name": "org/b"}],
        [
            {"workflow_runs": []},
            {"error": "GitHub API error: HTTP 403"},
        ],
    )

    assert payload["ok"] is True
    assert payload["count"] == 0
    assert payload["repos_scanned"] == 2
    assert payload["errors"] == ["org/b: GitHub API error: HTTP 403"]
    assert "Some repository workflow runs could not be fetched" in payload["message"]


def test_ci_payload_sorts_runs_by_latest_update():
    payload = build_ci_payload(
        [{"full_name": "org/repo"}],
        [{
            "workflow_runs": [
                {"id": 1, "name": "older", "status": "completed", "html_url": "https://github.com/org/repo/actions/runs/1", "updated_at": "2026-11-12T10:00:00Z"},
                {"id": 2, "name": "newer", "status": "in_progress", "html_url": "https://github.com/org/repo/actions/runs/2", "updated_at": "2026-11-13T10:00:00Z"},
            ],
        }],
    )

    assert [run["id"] for run in payload["runs"]] == [2, 1]


def test_async_github_api_calls_are_awaited_in_read_routes():
    tree = ast.parse(ADMIN_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }

    for name in ("list_repos", "list_pull_requests", "list_issues"):
        function = functions[name]
        awaited = any(
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "attr", None) == "_github_request"
            for node in ast.walk(function)
        )
        assert awaited, f"{name} must await the async GitHub API client"


def test_ci_endpoint_is_registered_and_uses_bounded_async_fanout():
    tree = ast.parse(ADMIN_PATH.read_text(encoding="utf-8"))
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_ci_runs"
    )
    route_registered = any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "get"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
        and decorator.args[0].value == "/ci"
        for decorator in function.decorator_list
    )
    assert route_registered
    source = ast.get_source_segment(ADMIN_PATH.read_text(encoding="utf-8"), function)
    assert "asyncio.gather" in source
    assert "min(int(max_repos), 20)" in source
