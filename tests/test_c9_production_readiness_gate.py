import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/c9_production_readiness_gate.py"


def run(env=None):
    merged = os.environ.copy()
    for key in ("KDO_C9_EVIDENCE_MANIFEST",):
        merged.pop(key, None)
    if env:
        merged.update(env)
    p = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, text=True, capture_output=True, env=merged)
    return p.returncode, json.loads(p.stdout)


def test_c95_blocks_when_external_evidence_is_missing():
    code, report = run()
    assert code == 2
    assert report["status"] == "BLOCKED"
    assert report["production_certified"] is False
    assert "real_postgresql_concurrency" in report["unproven"]
    assert "live_backup_restore" in report["unproven"]
    assert "real_provider_execution" in report["unproven"]
    assert "real_redis_recovery" in report["unproven"]


def test_c95_accepts_only_machine_readable_external_evidence_manifest(tmp_path):
    manifest = tmp_path / "evidence.json"
    manifest.write_text(json.dumps({"checks": {
        "real_postgresql_concurrency": {"status": "PASS", "evidence": "runner=pg-concurrency; run_id=abc"},
        "live_backup_restore": {"status": "PASS", "evidence": "runner=backup-restore; run_id=def"},
        "real_provider_execution": {"status": "PASS", "evidence": "runner=staging-provider; run_id=ghi"},
        "real_redis_recovery": {"status": "PASS", "evidence": "runner=redis-recovery; run_id=jkl"},
    }}), encoding="utf-8")
    code, report = run({"KDO_C9_EVIDENCE_MANIFEST": str(manifest)})
    assert code == 0
    assert report["status"] == "PASS"
    assert report["production_certified"] is True


def test_c95_does_not_treat_boolean_environment_flags_as_certification():
    code, report = run({
        "KDO_C9_REAL_POSTGRES_CERTIFIED": "true",
        "KDO_C9_BACKUP_RESTORE_CERTIFIED": "true",
        "KDO_C9_PROVIDER_CERTIFIED": "true",
        "KDO_C9_REDIS_CERTIFIED": "true",
    })
    assert code == 2
    assert report["status"] == "BLOCKED"
