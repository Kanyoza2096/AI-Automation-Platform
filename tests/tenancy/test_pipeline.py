"""Canonical pipeline behaviour."""

from core.request_context import RequestContext, clear_request_context, set_request_context
from core.pipeline import Decision, run_action_pipeline


def setup_function():
    clear_request_context()


def teardown_function():
    clear_request_context()


def test_pipeline_blocks_without_permission():
    set_request_context(
        RequestContext(
            principal_id="viewer-1",
            workspace_id="ws-1",
            role="viewer",
            permissions=("read",),
            species_privileges=False,
        )
    )
    result = run_action_pipeline(action="publish", payload={"text": "hi"}, permission="admin")
    assert result.allowed is False
    assert result.decision == Decision.BLOCK


def test_pipeline_allows_operator():
    set_request_context(
        RequestContext(
            principal_id="op-1",
            workspace_id="ws-1",
            role="operator",
            permissions=("read", "write"),
            species_privileges=False,
            correlation_id="c-1",
        )
    )
    result = run_action_pipeline(
        action="internal_note",
        payload={"text": "ok"},
        permission="write",
        enqueue=False,
    )
    assert result.allowed is True
    assert result.workspace_id == "ws-1"
    assert result.correlation_id == "c-1"
    assert result.decision in (Decision.ALLOW, Decision.AUTO_LOW, Decision.AUTO_MEDIUM, Decision.AUTO_HIGH)


def test_pipeline_requires_context():
    clear_request_context()
    try:
        run_action_pipeline(action="publish", payload={})
        assert False, "should raise"
    except RuntimeError:
        pass
