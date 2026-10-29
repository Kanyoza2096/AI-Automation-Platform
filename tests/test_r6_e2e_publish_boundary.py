from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_e2e_publish_is_disabled_in_production():
    source = (ROOT / "api/e2e_routes.py").read_text(encoding="utf-8")
    assert 'E2E publish routes are disabled in production' in source
    assert 'getattr(settings, "is_production", False)' in source


def test_e2e_real_publish_delegates_to_governed_helper():
    source = (ROOT / "api/e2e_routes.py").read_text(encoding="utf-8")
    assert "execute_governed_publish" in source
    real_start = source.index("from services.publish_orchestration import execute_governed_publish")
    real_branch = source[real_start:]
    assert "execute_governed_publish(" in real_branch
    assert "run_publish(" not in real_branch


def test_e2e_dry_run_remains_non_consequential():
    source = (ROOT / "api/e2e_routes.py").read_text(encoding="utf-8")
    dry_branch = source[source.index("if body.dry_run:"):source.index("from services.publish_orchestration import execute_governed_publish")]
    assert "dry_run=True" in dry_branch
    assert "durable=False" in dry_branch
