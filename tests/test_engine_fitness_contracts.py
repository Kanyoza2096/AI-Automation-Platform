import pytest
"""Engine fitness contracts for the final KDO architecture.

These are architectural invariants, not a claim of live-provider certification.
They prevent legacy engines from silently acquiring a second execution plane.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_director_is_fenced_and_publish_binding_uses_canonical_path():
    director = _read("services/director.py")
    bindings = _read("services/director_bindings.py")
    assert "set_authority_fence" in director
    assert "execute_governed_publish" in bindings
    assert "run_publish(" not in bindings


def test_condition_engine_boot_has_no_direct_content_fallback():
    lifecycle = _read("lifecycle.py")
    start = lifecycle.index("async def _boot_condition_engine")
    end = lifecycle.index("async def _boot_autonomous_content", start)
    block = lifecycle[start:end]
    assert "bind_condition_engine_with_council" in block
    assert "ConditionEngine fallback executor wired" not in block
    assert "content_engine.boost_content" not in block
    assert "content_engine.reduce_content" not in block
    assert "content_engine.safe_mode" not in block


def test_condition_engine_council_executor_is_durable_and_fail_closed():
    integration = _read("council/integration/condition_engine_integration.py")
    assert "deliberate_action" in integration
    assert "ExternalActionExecutor" in integration
    assert "ExecutionLedger" in integration
    assert "workspace_id_required_for_governed_execution" in integration
    assert "no_executor_for_action" in integration


def test_meta_engine_recovery_verifies_health_after_recovery():
    meta = _read("services/meta_engine.py")
    assert "_perform_recovery" in meta
    assert "check_engine_health(diagnosis.engine, force=True" in meta
    assert "Recovery completed but health verification failed" in meta


def test_content_engine_uses_single_ai_gateway_boundary():
    content = _read("services/content_engine.py")
    assert "get_llm_gateway" in content
    assert "complete(" in content
    assert "complete_async(" in content


def test_brand_scheduler_routes_publish_through_workflow_and_governed_publish():
    scheduler = _read("scheduler/brand_scheduler.py")
    workflow = _read("workflows/post_workflow.py")
    assert "PostPublishWorkflowFactory" in scheduler
    assert "execute_governed_publish" in workflow
    assert "run_publish(" not in workflow
    assert "recover_scheduled_post_claims" in scheduler
    assert "claim_scheduled_post" in scheduler


def test_no_direct_provider_call_in_brand_scheduler_publish_plane():
    scheduler = _read("scheduler/brand_scheduler.py")
    # The scheduler may notify configured webhooks, but it must not contain
    # provider-specific social API execution. Publishing is workflow-owned.
    forbidden = ("FacebookPlugin", "GraphAPI", "facebook.com/v", "api.twitter.com")
    assert not any(token in scheduler for token in forbidden)

@pytest.mark.asyncio
async def test_director_content_controls_converge_on_action_kernel(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    import services.director_bindings as bindings

    class FakeSupabase:
        client = object()
    container = MagicMock()
    container.supabase_service = FakeSupabase()

    kernel = MagicMock()
    kernel.execute = AsyncMock(return_value={"status": "succeeded", "action_type": "content_adjust_mix"})
    monkeypatch.setattr("services.action_kernel.build_default_action_kernel", lambda **_: kernel)

    handlers = bindings._build_handlers_for(MagicMock(), container)
    result = await handlers["optimize_content_schedule"]({
        "workspace_id": "ws-1", "brand_id": "brand-1", "content_mix_change": 0.2,
        "duration_posts": 5, "approved": True, "idempotency_key": "idem-1"
    })

    assert result["governed"] is True
    kernel.execute.assert_awaited_once()
    assert kernel.execute.await_args.kwargs["action_type"] == "content_adjust_mix"
    assert kernel.execute.await_args.kwargs["parameters"]["brand_id"] == "brand-1"


def test_director_content_bindings_do_not_call_content_engine_directly():
    bindings = _read("services/director_bindings.py")
    start = bindings.index("    async def optimize_content_schedule")
    end = bindings.index("    async def run_health_scan", start)
    block = bindings[start:end]
    assert "content_engine.boost_content" not in block
    assert "content_engine.reduce_content" not in block
    assert 'action_type="content_adjust_mix"' in block


def test_action_kernel_owns_content_control_contract():
    kernel = _read("services/action_kernel.py")
    assert '"content_adjust_mix"' in kernel
    assert "ActionContract(" in kernel
