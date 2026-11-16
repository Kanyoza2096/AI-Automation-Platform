import asyncio

from workflows.post_workflow import EmitEventStep


class RecordingBus:
    def __init__(self):
        self.calls = []

    async def publish(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "event-1"


def test_post_published_event_carries_explicit_workspace_and_brand():
    bus = RecordingBus()
    step = EmitEventStep(
        bus,
        workspace_id="ws-test",
        brand_id="brand-test",
    )

    result = asyncio.run(
        step.async_run(
            {
                "topic": "trust in God",
                "content_result": {"caption": "A grounded caption"},
                "publish_results": {
                    "facebook": {"ok": True, "post_id": "fb-post-1"},
                },
            }
        )
    )

    assert result == {"emitted": 1}
    assert len(bus.calls) == 1
    args, kwargs = bus.calls[0]
    assert args[0] == "post.published"
    assert kwargs["workspace_id"] == "ws-test"
    assert kwargs["brand_id"] == "brand-test"
    assert kwargs["strict_nerve"] is True


def test_post_published_event_fails_closed_without_workspace():
    bus = RecordingBus()
    step = EmitEventStep(bus, workspace_id=None, brand_id="brand-test")

    result = asyncio.run(
        step.async_run(
            {
                "topic": "trust in God",
                "content_result": {"caption": "A grounded caption"},
                "publish_results": {
                    "facebook": {"ok": True, "post_id": "fb-post-1"},
                },
            }
        )
    )

    assert result == {"emitted": 0, "error": "workspace_id_required"}
    assert bus.calls == []
