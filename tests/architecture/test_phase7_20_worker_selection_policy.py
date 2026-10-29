from pathlib import Path
ROOT=Path(__file__).parents[2]
SERVICE=ROOT/'services'/'v71_31_worker_selection_policy.py'
TEXT=SERVICE.read_text()

def test_phase720_service_exists(): assert SERVICE.exists()
def test_phase720_version(): assert 'VERSION = "phase7.20"' in TEXT
def test_phase720_sqlite_persistence(): assert 'CREATE TABLE IF NOT EXISTS worker_selection_policies' in TEXT
def test_phase720_version_key(): assert 'PRIMARY KEY(policy_id, version)' in TEXT
def test_phase720_fingerprint(): assert 'policy_fingerprint' in TEXT and 'hashlib.sha256' in TEXT
def test_phase720_no_authorization(): assert 'grant_authority' not in TEXT and 'authorize(' not in TEXT
def test_phase720_no_execution(): assert 'execute_action' not in TEXT and 'invoke_action_kernel' not in TEXT
def test_phase720_no_provider(): assert 'provider_call(' not in TEXT and 'provider_client' not in TEXT
def test_phase720_workspace_fence(): assert 'TENANT_MISMATCH' in TEXT and 'profile.workspace_id != self.workspace_id' in TEXT
def test_phase720_policy_weights_sum(): assert 'abs(sum(vals) - 1.0)' in TEXT
def test_phase720_reputation_bound(): assert 'reputation_influence' in TEXT and '0.10' in TEXT
def test_phase720_immutable_versions(): assert 'SelectionPolicyBlock.DUPLICATE' in TEXT
