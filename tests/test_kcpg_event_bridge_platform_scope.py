"""Regression coverage for trusted scope propagation in KCPG event bridge."""
import asyncio
import json

from services.kcpg_event_bridge import KCPGEventBridge


class FakeResponse:
    status_code = 202
    text = "accepted"


class FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


def test_platform_scoped_director_event_uses_trusted_envelope_scope():
    bridge = KCPGEventBridge(event_bus=object())
    bridge.url = "https://gateway.example/internal/events"
    bridge.token = "test-token"
    bridge.signing_secret = "test-signing-secret"
    bridge._client = FakeClient()

    event = {
        "event_id": "evt-platform-recovery-1",
        "event_name": "director.automation_job_recovery.requested",
        "workspace_id": "platform",
        "correlation_id": "job-recovery-test",
        "payload": {
            "trigger": "automation_job_failure_threshold",
            "failed_jobs": 9,
            "automatic_retry": False,
            "publication_replay_allowed": False,
        },
    }
    asyncio.run(bridge._on_event(event))

    assert len(bridge._client.calls) == 1
    url, request = bridge._client.calls[0]
    assert url == bridge.url
    assert request["headers"]["Authorization"] == "Bearer test-token"
    assert request["headers"]["X-KCPG-Event-Signature"]
    body = json.loads(request["content"])
    assert body["workspace_id"] == "platform"
    assert body["type"] == "director.automation_job_recovery.requested"
    assert body["automatic_retry"] is False
    assert body["publication_replay_allowed"] is False


def test_event_without_payload_or_envelope_scope_is_still_dropped():
    bridge = KCPGEventBridge(event_bus=object())
    bridge.url = "https://gateway.example/internal/events"
    bridge.token = "test-token"
    bridge.signing_secret = "test-signing-secret"
    bridge._client = FakeClient()

    asyncio.run(bridge._on_event({
        "event_id": "evt-unscoped",
        "event_name": "tenant.event",
        "payload": {"value": 1},
    }))

    assert bridge._client.calls == []
