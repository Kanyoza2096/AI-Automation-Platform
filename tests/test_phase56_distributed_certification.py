from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_action_outbox_has_explicit_lease_fencing():
    sql = (ROOT / "migrations/047_action_outbox_lease_fencing.sql").read_text()
    assert "lease_token" in sql
    assert "lease_owner" in sql
    assert "lease_expires_at" in sql
    assert "finalize_action_outbox" in sql

def test_worker_claims_with_stable_worker_identity():
    src = (ROOT / "services/action_outbox_worker.py").read_text()
    assert '"p_worker_id": self.worker_id' in src
    assert "finalize_action_outbox" in src

def test_terminal_finalization_requires_lease_token():
    sql = (ROOT / "migrations/047_action_outbox_lease_fencing.sql").read_text()
    assert "AND lease_token = p_lease_token" in sql

def test_production_workers_include_action_outbox_worker():
    src = (ROOT / "workers/runtime.py").read_text()
    assert "run_action_outbox" in src
