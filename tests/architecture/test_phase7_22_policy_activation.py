from pathlib import Path
ROOT=Path(__file__).parents[2]; SERVICE=ROOT/'services'/'v71_33_worker_selection_policy_activation.py'; TEXT=SERVICE.read_text()
def test_exists(): assert SERVICE.exists()
def test_version(): assert 'VERSION = "phase7.22"' in TEXT
def test_durable_table(): assert 'CREATE TABLE IF NOT EXISTS worker_selection_policy_activation' in TEXT
def test_fingerprint(): assert 'binding_fingerprint' in TEXT and 'hashlib.sha256' in TEXT
def test_active_gate(): assert 'change.state != "ACTIVE"' in TEXT and 'NOT_ACTIVE' in TEXT
def test_policy_fingerprint_binding(): assert 'change.policy_fingerprint != policy.policy_fingerprint' in TEXT
def test_workspace(): assert 'TENANT_MISMATCH' in TEXT and 'workspace_id != ws' in TEXT
def test_allowlist(): assert 'CAPABILITY_BLOCKED' in TEXT and 'allowed_capabilities' in TEXT
def test_fail_closed(): assert 'BINDING_MISSING' in TEXT and 'enforce' in TEXT
def test_authority_false(): assert 'authority_granted": False' in TEXT and 'execution_requested": False' in TEXT
def test_no_execute(): assert 'execute_action' not in TEXT and 'invoke_action_kernel' not in TEXT
def test_no_provider(): assert 'provider_call(' not in TEXT and 'provider_client' not in TEXT
def test_no_authorize(): assert 'authorize(' not in TEXT and 'grant_authority' not in TEXT
