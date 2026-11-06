"""Regression tests for Director event publication lifecycle."""

import asyncio

from services.director import utils


def test_director_event_publication_is_tracked_and_drained(monkeypatch):
    published = []

    class FakeBus:
        async def publish(self, event_name, payload, **kwargs):
            await asyncio.sleep(0)
            published.append((event_name, payload, kwargs))
            return "evt-test"

    async def get_bus():
        return FakeBus()

    monkeypatch.setattr(utils, "HAS_EVENT_BUS", True)
    monkeypatch.setattr(utils, "get_unified_bus", get_bus)

    async def scenario():
        utils._publish_event(
            "director.proposal",
            {
                "proposal_id": "proposal-test",
                "workspace_id": "workspace-test",
                "correlation_id": "corr-test",
                "parent_event_id": "evt-parent",
                "brand_id": "brand-test",
                "decision_id": "decision-test",
            },
        )
        assert utils._pending_event_tasks
        timed_out = await utils.flush_pending_event_tasks(timeout_seconds=1.0)
        assert timed_out == 0
        assert not utils._pending_event_tasks

    asyncio.run(scenario())

    assert len(published) == 1
    assert published[0][0] == "director.proposal"
    assert published[0][1]["workspace_id"] == "workspace-test"
    assert published[0][2]["source"] == "director"
    assert published[0][2]["workspace_id"] == "workspace-test"
    assert published[0][2]["correlation_id"] == "corr-test"
    assert published[0][2]["parent_event_id"] == "evt-parent"
    assert published[0][2]["brand_id"] == "brand-test"
    assert published[0][2]["decision_id"] == "decision-test"


def test_director_event_drain_rejects_negative_timeout():
    try:
        asyncio.run(utils.flush_pending_event_tasks(timeout_seconds=-0.1))
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative event drain timeout must be rejected")
