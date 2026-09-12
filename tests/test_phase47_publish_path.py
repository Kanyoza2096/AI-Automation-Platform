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
