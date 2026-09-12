from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def test_production_requirements_cover_preflight_dependencies():
    text = (ROOT / "requirements.txt").read_text()
    for dep in ("gunicorn", "supabase", "redis", "python-socketio", "PyPDF2", "Flask", "stripe", "google-auth", "google-api-python-client", "psycopg[binary]"):
        assert re.search(rf"^\s*{re.escape(dep)}(?:\[[^\]]+\])?(?:[<>=!~]|\s|$)", text, re.M | re.I), dep

def test_production_compose_uses_multiworker_api_and_live_healthcheck():
    text = (ROOT / "deploy/docker-compose.production.yml").read_text()
    assert "gunicorn main:app" in text
    assert "/health/live" in text
    assert "SERVICE_MODE: api" in text
    assert "SERVICE_MODE: core" in text
    assert "SERVICE_MODE: workers" in text

def test_migration_history_has_deterministic_numeric_filename_order():
    files = sorted((ROOT / "migrations").glob("*.sql"), key=lambda p: (int(re.match(r"^(\d+)_", p.name).group(1)), p.name))
    keys = [(int(re.match(r"^(\d+)_", p.name).group(1)), p.name) for p in files]
    assert keys == sorted(keys)
