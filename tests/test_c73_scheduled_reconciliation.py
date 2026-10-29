from pathlib import Path


def test_scheduled_claim_migration_has_atomic_claim_and_outbox_aware_recovery():
    sql = Path("migrations/109_v70_8_scheduled_post_claim_reconciliation.sql").read_text()
    assert "claim_scheduled_post" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "s.outbox_id IS NOT NULL THEN 'outbox_pending'" in sql
    assert "status = 'processing'" in sql
    assert "lease_expires_at" in sql


def test_scheduler_no_longer_resets_processing_by_wall_clock():
    src = Path("scheduler/brand_scheduler.py").read_text()
    assert 'rpc("recover_scheduled_post_claims"' in src
    assert 'rpc("claim_scheduled_post"' in src
    assert '.lt("started_at", stale_before)' not in src
    assert 'eq("lease_owner", worker_id)' in src


def test_scheduled_publish_uses_stable_idempotency_identity():
    src = Path("workflows/post_workflow.py").read_text()
    assert 'f"scheduled:{context.get(\'scheduled_post_id\')}"' in src
    assert 'idempotency_key=' in src


def test_scheduled_finalizer_only_completes_processing_or_outbox_pending():
    src = Path("core/publish_outbox.py").read_text()
    assert '.in_("status", ["processing", "outbox_pending"])' in src
