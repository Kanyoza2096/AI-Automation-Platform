from types import SimpleNamespace

from services.external_reconciliation import ExternalReconciler


def item(post_id="p1", platform="facebook"):
    return SimpleNamespace(platform_post_id=post_id, platform=platform, social_account=object())


def test_no_post_id_is_never_inferred():
    result = ExternalReconciler().reconcile(item(post_id=None))
    assert result.status == "unknown"


def test_missing_lookup_is_fail_closed():
    class C: pass
    result = ExternalReconciler().reconcile(item(), connector=C())
    assert result.status == "unknown"


def test_positive_provider_confirmation():
    class C:
        def lookup_post(self, post_id): return True
    result = ExternalReconciler().reconcile(item(), connector=C())
    assert result.status == "published"
    assert result.post_id == "p1"


def test_provider_not_found_is_explicit():
    class C:
        def lookup_post(self, post_id): return False
    result = ExternalReconciler().reconcile(item(), connector=C())
    assert result.status == "not_found"


def test_inconclusive_provider_response_stays_unknown():
    class C:
        def lookup_post(self, post_id): return None
    result = ExternalReconciler().reconcile(item(), connector=C())
    assert result.status == "unknown"


def test_single_not_found_does_not_requeue_uncertain_publish():
    from core.publish_outbox import PublishOutbox
    obx = PublishOutbox()
    from core.publish_outbox import OutboxItem
    created = OutboxItem(id="o1", workspace_id="ws", decision_id="d", correlation_id="c", platform="facebook", status="uncertain", platform_post_id="p1")
    obx._mem[created.id] = created

    class C:
        def lookup_post(self, post_id): return False

    obx.reconcile_with_connector(created.id, "ws", C())
    assert obx.get(created.id, workspace_id="ws").status == "uncertain"
    assert obx.get(created.id, workspace_id="ws").consecutive_not_found == 1


def test_repeated_authoritative_not_found_allows_controlled_retry():
    from core.publish_outbox import PublishOutbox
    obx = PublishOutbox()
    from core.publish_outbox import OutboxItem
    created = OutboxItem(id="o1", workspace_id="ws", decision_id="d", correlation_id="c", platform="facebook", status="uncertain", platform_post_id="p1")
    obx._mem[created.id] = created

    class C:
        def lookup_post(self, post_id): return False

    obx.reconcile_with_connector(created.id, "ws", C())
    obx.reconcile_with_connector(created.id, "ws", C())
    resolved = obx.get(created.id, workspace_id="ws")
    assert resolved.status == "pending"
    assert resolved.consecutive_not_found == 2
