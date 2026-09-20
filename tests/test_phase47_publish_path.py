import asyncio
from types import SimpleNamespace


def test_autonomous_routing_includes_publish_command():
    from core.autonomous_loop import AutonomousLoop
    src = open("core/autonomous_loop.py", encoding="utf-8").read()
    assert '"publish_command": self._handle_publish_command' in src


def test_post_workflow_uses_actual_source_and_scheduled_caption():
    from workflows.post_workflow import GenerateContentStep
    # Structural contract: the implementation must expose the scheduled override.
    src = open("workflows/post_workflow.py", encoding="utf-8").read()
    assert 'context.get("scheduled_caption")' in src
    assert 'source=source' in src
    assert 'scheduled_post_id=context.get("scheduled_post_id")' in src

def test_outbox_has_scheduled_reconciler():
    src = open("core/publish_outbox.py", encoding="utf-8").read()
    assert "def _finalize_scheduled_post" in src
    assert '"published"' in src
    assert '"uncertain"' in src


def test_trend_publish_bridge_uses_action_kernel_instead_of_direct_publish_outbox():
    from pathlib import Path
    text = Path("services/trend_publish_bridge.py").read_text()
    assert "build_default_action_kernel" in text
    assert "kernel.enqueue(" in text
    assert "outbox.enqueue(" not in text
    assert "execution_requested\": False" in text


def test_trend_publish_bridge_admits_publish_only_through_governed_kernel(monkeypatch):
    import asyncio
    import services.trend_publish_bridge as bridge

    class FakeKernel:
        calls = []
        async def enqueue(self, **kwargs):
            self.calls.append(kwargs)
            return {"status": "approval_required", "accepted": False, "intent_id": "act-1"}

    kernel = FakeKernel()
    monkeypatch.setattr(
        "services.action_kernel.build_default_action_kernel",
        lambda **kwargs: kernel,
    )
    monkeypatch.setattr(
        "services.supabase.supabase_service",
        type("S", (), {"service_client": object(), "client": object()})(),
    )
    result = asyncio.run(bridge.handle_trend_publish_requested({
        "workspace_id": "ws-1", "brand_id": "b-1", "topic": "trend",
        "content": "draft", "job_id": "job-1", "decision_id": "dec-1",
    }))
    assert result["governed"] is True
    assert result["status"] == "approval_required"
    assert result["accepted"] is False
    assert kernel.calls[0]["action_type"] == "publish_content"
    assert kernel.calls[0]["approved"] is False


def test_content_publish_surface_has_no_direct_provider_publish_bypass():
    """Content-facing orchestration must enter the governed publish boundary."""
    from pathlib import Path

    surfaces = {
        "services/v70_6_content_marketing_operating_loop.py": Path("services/v70_6_content_marketing_operating_loop.py").read_text(),
        "services/trend_publish_bridge.py": Path("services/trend_publish_bridge.py").read_text(),
        "workflows/post_workflow.py": Path("workflows/post_workflow.py").read_text(),
        "api/v1.py": Path("api/v1.py").read_text(),
    }
    forbidden = (
        "PublishOutbox(",
        ".publish_all(",
        "facebook.post_to_page(",
        "twitter.post(",
        "linkedin.post(",
    )
    for name, text in surfaces.items():
        assert not any(token in text for token in forbidden), f"publish bypass in {name}"


def test_public_post_and_workflow_enter_governed_publish_boundary():
    from pathlib import Path

    api = Path("api/v1.py").read_text()
    workflow = Path("workflows/post_workflow.py").read_text()
    assert "execute_governed_publish(" in api
    assert "execute_governed_publish(" in workflow
    assert "run_publish(" not in workflow
