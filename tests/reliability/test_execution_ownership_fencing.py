from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_completion_passes_worker_identity_to_durable_rpc():
    source = (ROOT / "services/execution_ledger.py").read_text()
    marker = '"p_execution_id": updated.execution_id,'
    assert marker in source
    section = source[source.index(marker):source.index("}).execute()", source.index(marker))]
    assert '"p_worker_id": self.worker_id' in section


def test_ownership_fencing_migration_requires_live_worker_lease():
    sql = (ROOT / "migrations/035_execution_ownership_fencing.sql").read_text()
    assert "p_worker_id TEXT" in sql
    assert "r.locked_by IS DISTINCT FROM p_worker_id" in sql
    assert "r.locked_until <= now()" in sql
    assert "complete_external_execution(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB)" in sql
