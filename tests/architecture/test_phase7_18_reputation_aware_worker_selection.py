from pathlib import Path

ROOT = Path(__file__).parents[2]
SERVICE = ROOT / "services" / "v71_29_reputation_aware_worker_selection.py"
TEXT = SERVICE.read_text()


def test_phase718_service_exists():
    assert SERVICE.exists()


def test_phase718_declares_version():
    assert 'VERSION = "phase7.18"' in TEXT


def test_phase718_has_no_authorization_path():
    assert "authorize(" not in TEXT
    assert "grant_authority" not in TEXT


def test_phase718_has_no_execution_path():
    assert "invoke_action_kernel" not in TEXT
    assert "execute_action" not in TEXT


def test_phase718_has_no_provider_path():
    assert "provider_client" not in TEXT
    assert "provider_call(" not in TEXT


def test_phase718_uses_canonical_base_selection_first():
    assert "self.base_selection.select(" in TEXT


def test_phase718_reputation_cannot_override_eligibility():
    assert "for candidate in base.candidates" in TEXT
    assert "ranked: list[WorkerCandidate] = []" in TEXT


def test_phase718_influence_is_bounded():
    assert "<= 0.10" in TEXT
    assert "max_influence: float = 0.05" in TEXT
