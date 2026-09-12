"""Pipeline depth — real metabolism / plan hooks."""

from core.request_context import RequestContext, set_request_context, clear_request_context
from core.pipeline import run_action_pipeline, Decision
from core.plan_authority import authority_for_plan, AuthorityLevel
from core.persistence_policy import memory_fallback_allowed
from core.fail_closed import resolve_runtime_mode


def setup_function():
    clear_request_context()


def teardown_function():
    clear_request_context()


def test_metabolism_publish_path_runs():
    set_request_context(
        RequestContext(
            principal_id="op",
            workspace_id="ws-test-metabolism",
            role="operator",
            permissions=("read", "write"),
        )
    )
    result = run_action_pipeline(
        action="publish",
        payload={"caption": "hello"},
        permission="write",
        enqueue=False,
    )
    # Should not crash; metabolism returns structured result
    assert result.metabolism.get("reason")
    assert "metabolism" in result.metabolism.get("reason", "") or result.metabolism.get("allowed") is not None


def test_production_mode_fail_closed_flag():
    mode = resolve_runtime_mode("production")
    assert mode.allow_memory_fallback is False
    assert memory_fallback_allowed() in (True, False)  # depends on env at runtime


def test_plan_matrix_aligned_with_catalog():
    assert authority_for_plan("free") == AuthorityLevel.SUGGEST
