from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_release_integrity_gate_exists_and_checks_non_root_container():
    gate = ROOT / "scripts/release_integrity_gate.py"
    text = gate.read_text(encoding="utf-8")
    assert '"USER botuser"' in text
    assert '"HEALTHCHECK"' in text


def test_production_artifact_excludes_secrets_and_test_payloads():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env*" in text
    assert "tests/" in text
    assert "__pycache__/" in text


def test_production_compose_has_distinct_service_modes_and_restart_policy():
    text = (ROOT / "deploy/docker-compose.production.yml").read_text(encoding="utf-8")
    for needle in ("SERVICE_MODE: api", "SERVICE_MODE: core", "SERVICE_MODE: workers", "restart: unless-stopped"):
        assert needle in text
