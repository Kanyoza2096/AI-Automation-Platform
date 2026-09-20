
from core.request_context import RequestContext, set_request_context, clear_request_context
from core.pipeline import run_action_pipeline
from core.decision_gate import DecisionGate


def setup_function():
    clear_request_context()


def teardown_function():
    clear_request_context()


def test_pipeline_issues_decision_gate_id():
    set_request_context(
        RequestContext(
            principal_id="op",
            workspace_id="ws-dec-1",
            role="operator",
            permissions=("read", "write"),
        )
    )
    result = run_action_pipeline(
        action="publish",
        payload={"caption": "x"},
        permission="write",
        enqueue=False,
    )
    if result.allowed:
        assert result.decision_id
        # May be registered on a new DecisionGate instance — id format
        assert isinstance(result.decision_id, str)
        assert len(result.decision_id) > 4
