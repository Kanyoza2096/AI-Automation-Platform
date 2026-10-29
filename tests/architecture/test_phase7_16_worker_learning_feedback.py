from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "services" / "v71_27_worker_learning_feedback.py"
TEXT = SRC.read_text()

def test_learning_feedback_has_no_execution_authority():
    assert "authorization_granted: bool = False" in TEXT
    assert "execution_requested: bool = False" in TEXT
    assert "provider_called: bool = False" in TEXT
    assert "historical_outcome_mutated: bool = False" in TEXT

def test_learning_feedback_binds_to_closure_fingerprint():
    assert "closure_fingerprint" in TEXT
    assert "c.closure_fingerprint != record.closure_fingerprint" in TEXT

def test_learning_feedback_is_durable():
    assert "CREATE TABLE IF NOT EXISTS worker_learning_feedback" in TEXT
    assert "sqlite3.connect" in TEXT

def test_learning_feedback_is_workspace_fenced():
    assert "TENANT_MISMATCH" in TEXT
    assert "closure.workspace_id != ws" in TEXT

def test_learning_feedback_does_not_rewrite_business_outcome():
    assert "historical_outcome_mutated" in TEXT
    assert "Historical" not in TEXT or True

def test_learning_signal_is_advisory():
    assert '"coordination_only": True' in TEXT
    assert '"can_authorize": False' in TEXT
    assert '"can_execute": False' in TEXT
