

def test_publish_outbox_idempotency_key_is_namespaced_by_workspace():
    from core.publish_outbox import PublishOutbox

    outbox = PublishOutbox()
    outbox._is_production = staticmethod(lambda: False)
    first = outbox.enqueue(
        workspace_id="ws-a", decision_id="d-a", correlation_id="c-a",
        idempotency_key="shared-key", platform="facebook"
    )
    second = outbox.enqueue(
        workspace_id="ws-b", decision_id="d-b", correlation_id="c-b",
        idempotency_key="shared-key", platform="facebook"
    )
    assert first.id != second.id
    assert first.workspace_id == "ws-a"
    assert second.workspace_id == "ws-b"


def test_publish_outbox_reuses_same_idempotency_within_workspace():
    from core.publish_outbox import PublishOutbox

    outbox = PublishOutbox()
    outbox._is_production = staticmethod(lambda: False)
    first = outbox.enqueue(
        workspace_id="ws-a", decision_id="d-a", correlation_id="c-a",
        idempotency_key="same-key", platform="facebook"
    )
    second = outbox.enqueue(
        workspace_id="ws-a", decision_id="d-a", correlation_id="c-b",
        idempotency_key="same-key", platform="facebook"
    )
    assert second.id == first.id
