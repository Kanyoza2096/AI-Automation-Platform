from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "020_platform_state_snapshots.sql"
)


def test_platform_state_migration_defines_reader_schema_and_atomic_writer():
    sql = MIGRATION.read_text(encoding="utf-8")
    normalized = " ".join(sql.lower().split())

    assert "create table if not exists public.platform_state_snapshots" in normalized
    assert "primary key (scope_type, scope_id)" in normalized
    assert "state jsonb not null" in normalized
    assert "create or replace function public.write_platform_state_snapshot" in normalized
    assert "p_expected_revision bigint" in normalized
    assert "where scope_type = p_scope_type" in normalized
    assert "and revision = p_expected_revision" in normalized
    assert "platform_state_revision_conflict" in normalized
    assert "using errcode = '40001'" in normalized


def test_platform_state_migration_restricts_access_to_service_role():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())

    assert "enable row level security" in sql
    assert "revoke all on table public.platform_state_snapshots from public, anon, authenticated" in sql
    assert "grant select on table public.platform_state_snapshots to service_role" in sql
    assert "grant execute on function public.write_platform_state_snapshot(text, text, bigint, jsonb) to service_role" in sql
