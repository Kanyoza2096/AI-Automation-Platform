from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


def test_expired_lease_is_quarantined_not_retried():
    sql = open("migrations/024_phase6_failure_storm.sql", encoding="utf-8").read()
    assert "status='uncertain'" in sql
    assert "Worker lease expired; reconcile before retry" in sql
    assert "WHERE status='pending'" in sql


def test_durable_finalization_requires_worker_lease():
    sql = open("migrations/024_phase6_failure_storm.sql", encoding="utf-8").read()
    assert "status = 'processing' AND locked_by = p_locked_by" in sql
    assert "complete_publish_outbox" in sql
    assert "fail_publish_outbox" in sql


@pytest.mark.asyncio
async def test_lost_lease_never_claims_success():
    from core.publish_outbox import PublishOutbox, OutboxItem
    obx = PublishOutbox(supabase_client=MagicMock())
    item = OutboxItem(id="x", workspace_id="ws", decision_id="d", correlation_id="c", locked_by="worker-a")
    with patch.object(obx._db, "rpc") as rpc:
        resp = MagicMock(); resp.execute.return_value.data = [{"updated": False}]
        rpc.return_value = resp
        assert obx._finalize_durable(item, status="published", post_id="p") is False


def test_uncertain_state_is_not_eligible_for_automatic_retry():
    from core.publish_outbox import OutboxItem, PublishOutbox
    obx = PublishOutbox()
    item = OutboxItem(id="uncertain", workspace_id="ws", decision_id="d", correlation_id="c", status="uncertain")
    obx._mem[item.id] = item
    assert obx.claim_batch() == []


def test_reconciliation_is_explicit():
    from core.publish_outbox import OutboxItem, PublishOutbox
    obx = PublishOutbox()
    item = OutboxItem(id="u", workspace_id="ws", decision_id="d", correlation_id="c", status="uncertain")
    obx._mem[item.id] = item
    resolved = obx.reconcile("u", "ws", resolution="confirmed_not_published", note="provider lookup clear")
    assert resolved.status == "pending"
    assert obx.claim_batch(limit=1)[0].id == "u"
