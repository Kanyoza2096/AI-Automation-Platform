import json
import subprocess
import sys

from organism.staging_validation import ProbeStatus, StagingValidationHarness


def test_staging_harness_requires_all_authoritative_probes():
    report = StagingValidationHarness().run({})
    assert report.passed is False
    assert set(report.unobservable) == set(StagingValidationHarness.REQUIRED_PROBES)


def test_live_staging_runner_fails_closed_without_explicit_live_flag():
    proc = subprocess.run(
        [sys.executable, "scripts/staging_production_certification.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["status"] == "blocked"


def test_backup_restore_runner_does_not_claim_live_certification_offline():
    proc = subprocess.run(
        [sys.executable, "scripts/backup_restore_certification.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["status"] == "not_proven"
