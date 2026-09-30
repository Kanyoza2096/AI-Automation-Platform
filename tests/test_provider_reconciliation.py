from core.publish_outbox import OutboxItem, PublishOutbox
from services.provider_reconciliation import ProviderReconciliationService


def test_provider_event_requires_exact_provider_id():
    obx = PublishOutbox()
    item = OutboxItem(id="o1", workspace_id="ws", decision_id="d", correlation_id="c", platform="whatsapp", status="uncertain", platform_post_id="wamid.1")
    obx._mem[item.id] = item
    result = ProviderReconciliationService(obx).apply_event(
        platform="whatsapp", provider_post_id="wamid.other", status="sent", workspace_id="ws"
    )
    assert result["matched"] is False
    assert obx.get("o1", workspace_id="ws").status == "uncertain"


def test_whatsapp_sent_webhook_resolves_exact_uncertain_item():
    obx = PublishOutbox()
    item = OutboxItem(id="o1", workspace_id="ws", decision_id="d", correlation_id="c", platform="whatsapp", status="uncertain", platform_post_id="wamid.1")
    obx._mem[item.id] = item
    result = ProviderReconciliationService(obx).apply_event(
        platform="whatsapp", provider_post_id="wamid.1", status="sent", workspace_id="ws"
    )
    assert result["matched"] is True
    assert obx.get("o1", workspace_id="ws").status == "published"


def test_provider_failure_does_not_requeue_ambiguous_publish_automatically():
    obx = PublishOutbox()
    item = OutboxItem(id="o1", workspace_id="ws", decision_id="d", correlation_id="c", platform="whatsapp", status="uncertain", platform_post_id="wamid.1")
    obx._mem[item.id] = item
    result = ProviderReconciliationService(obx).apply_event(
        platform="whatsapp", provider_post_id="wamid.1", status="failed", workspace_id="ws", evidence="Meta failure"
    )
    assert result["status"] == "failed"
    assert obx.get("o1", workspace_id="ws").status == "failed"


def test_unknown_provider_status_remains_uncertain():
    obx = PublishOutbox()
    item = OutboxItem(id="o1", workspace_id="ws", decision_id="d", correlation_id="c", platform="whatsapp", status="uncertain", platform_post_id="wamid.1")
    obx._mem[item.id] = item
    result = ProviderReconciliationService(obx).apply_event(
        platform="whatsapp", provider_post_id="wamid.1", status="queued", workspace_id="ws"
    )
    assert result["status"] == "uncertain"
