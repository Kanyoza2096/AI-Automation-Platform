from pathlib import Path
ROOT=Path(__file__).parents[2]; SERVICE=ROOT/'services'/'v71_32_worker_selection_policy_governance.py'; TEXT=SERVICE.read_text()
def test_exists(): assert SERVICE.exists()
def test_version(): assert 'VERSION = "phase7.21"' in TEXT
def test_sqlite(): assert 'CREATE TABLE IF NOT EXISTS worker_selection_policy_changes' in TEXT
def test_fingerprint(): assert 'change_fingerprint' in TEXT and 'hashlib.sha256' in TEXT
def test_states(): assert 'PROPOSED' in TEXT and 'REVIEWED' in TEXT and 'APPROVED' in TEXT and 'ACTIVE' in TEXT and 'SUPERSEDED' in TEXT and 'ROLLED_BACK' in TEXT
def test_roles(): assert 'policy_owner' in TEXT and 'policy_reviewer' in TEXT and 'policy_approver' in TEXT
def test_sod(): assert 'ROLE_REQUIRED' in TEXT and 'role not in allowed' in TEXT
def test_active_conflict(): assert 'ACTIVE_CONFLICT' in TEXT and "state='ACTIVE'" in TEXT
def test_workspace(): assert 'workspace_id != self.workspace_id' in TEXT
def test_tamper(): assert 'change.change_fingerprint != change.fingerprint()' in TEXT
def test_no_execute(): assert 'execute_action' not in TEXT and 'invoke_action_kernel' not in TEXT
def test_no_provider(): assert 'provider_call(' not in TEXT and 'provider_client' not in TEXT
def test_no_authorization(): assert 'grant_authority' not in TEXT and 'authorize(' not in TEXT
def test_durable_transition(): assert '_replace(updated)' in TEXT
def test_approval_separation(): assert 'Approval and activation are separate' in TEXT
