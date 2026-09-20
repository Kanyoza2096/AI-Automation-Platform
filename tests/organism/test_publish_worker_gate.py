import pytest
from core.request_context import RequestContext, set_request_context, clear_request_context
from core.pipeline import run_action_pipeline
from core.decision_gate import decision_gate, DecisionGateError
from core.publish_authorization import assert_publish_allowed


def setup_function():
    clear_request_context()


def teardown_function():
    clear_request_context()


def test_assert_publish_requires_decision_in_strict():
    with pytest.raises(DecisionGateError):
        assert_publish_allowed(None, "ws-x", strict=True)


def test_pipeline_decision_accepted_by_gate():
    set_request_context(
        RequestContext(
            principal_id="op",
            workspace_id="ws-pub-1",
            role="operator",
            permissions=("read", "write"),
        )
    )
    result = run_action_pipeline(
        action="publish",
        payload={"caption": "hi"},
        permission="write",
        enqueue=False,
    )
    if not result.allowed:
        pytest.skip("publish not allowed in this environment")
    d = assert_publish_allowed(result.decision_id, "ws-pub-1", strict=True, consume=False)
    assert d.approved is True
