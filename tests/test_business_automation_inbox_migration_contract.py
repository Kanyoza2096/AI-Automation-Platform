from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "074_v69_18_business_automation_inbox_alignment.sql"
)


def test_business_automation_inbox_fanout_is_installed_on_canonical_event_store():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "after insert on public.events" in sql
    assert "insert into public.event_deliveries" in sql
    assert "'business_automation'" in sql
    assert "on conflict (event_id, consumer_group) do nothing" in sql
    assert "business_automation_event_fanout_ready" in sql


def test_claim_rpc_uses_lease_and_skip_locked_with_fencing_token():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "for update of d skip locked" in sql
    assert "p_lease_seconds" in sql
    assert "d.claimed_at < now() - make_interval" in sql
    assert "d.claimed_by" in sql
    assert "claim_token text" in sql


def test_completion_rpc_requires_current_claim_token():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "p_claim_token text" in sql
    assert "and d.claimed_by = nullif" in sql
    assert "revoke all on function public.complete_business_automation_event_delivery(uuid, text, boolean, text)" in sql
    assert "business_automation_stale_reservation_guard_ready" in sql


def test_migration_does_not_backfill_or_mutate_existing_event_history():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "does not backfill historical events" in sql
    assert "after insert on public.events" in sql
    assert "delete from public.events" not in sql
    assert "update public.events" not in sql
