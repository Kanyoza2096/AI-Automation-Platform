from pathlib import Path
ROOT=Path(__file__).parents[2]
SERVICE=ROOT/"services"/"v71_30_worker_selection_explainability.py"
TEXT=SERVICE.read_text()

def test_phase719_service_exists(): assert SERVICE.exists()
def test_phase719_version(): assert 'VERSION = "phase7.19"' in TEXT
def test_phase719_no_authorization(): assert "authorize(" not in TEXT and "grant_authority" not in TEXT
def test_phase719_no_execution(): assert "invoke_action_kernel" not in TEXT and "execute_action" not in TEXT
def test_phase719_no_provider(): assert "provider_client" not in TEXT and "provider_call(" not in TEXT
def test_phase719_observes_canonical_selection(): assert "self.reputation_selection.get(sid)" in TEXT
def test_phase719_binds_to_source_fingerprint(): assert "source_selection_fingerprint" in TEXT
def test_phase719_persists_evidence(): assert "CREATE TABLE IF NOT EXISTS worker_selection_decision_evidence" in TEXT
def test_phase719_detects_tamper(): assert "EVIDENCE_TAMPERED" in TEXT and "evidence.fingerprint()" in TEXT
def test_phase719_authority_flags_are_false(): assert "authority_granted: bool = False" in TEXT and "execution_requested: bool = False" in TEXT
def test_phase719_exposes_binding_constraints(): assert "binding_constraints" in TEXT
def test_phase719_deterministic_rank_evidence(): assert "rank=index + 1" in TEXT
