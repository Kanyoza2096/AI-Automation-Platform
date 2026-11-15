"""Regression tests for async workflow-trigger dispatch."""

import ast
from pathlib import Path


def test_workflow_trigger_awaits_scheduler_manager_result():
    """The async manager must be awaited before its result is treated as a dict."""
    source_path = (
        Path(__file__).resolve().parents[1]
        / "api"
        / "routers"
        / "workflows.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))

    workflow_trigger = next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "workflow_trigger"
    )
    parents = {
        child: parent
        for parent in ast.walk(workflow_trigger)
        for child in ast.iter_child_nodes(parent)
    }
    calls = [
        node
        for node in ast.walk(workflow_trigger)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "trigger_for_brand"
    ]

    assert len(calls) == 1
    assert isinstance(parents[calls[0]], ast.Await)
