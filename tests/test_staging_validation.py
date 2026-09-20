import pytest
from organism.staging_validation import (
    ChaosScenario, ProbeResult, ProbeStatus, StagingValidationHarness, fail_closed_probe,
)


def _all_ok():
    return {name: (lambda: True) for name in StagingValidationHarness.REQUIRED_PROBES}


def test_harness_requires_real_staging_environment():
    with pytest.raises(ValueError):
        StagingValidationHarness(environment="production")


def test_complete_authoritative_probe_set_passes():
    report = StagingValidationHarness().run(_all_ok())
    assert report.passed is True
    assert report.failures == ()
    assert report.unobservable == ()


def test_missing_probe_is_unobservable_not_healthy():
    probes = _all_ok()
    probes.pop("redis")
    report = StagingValidationHarness().run(probes)
    assert report.passed is False
    assert "redis" in report.unobservable


def test_probe_exception_fails_closed():
    def broken():
        raise ConnectionError("redis down")
    probes = _all_ok()
    probes["redis"] = broken
    report = StagingValidationHarness().run(probes)
    assert "redis" in report.failures
    assert report.passed is False


def test_none_probe_result_is_unobservable():
    probes = _all_ok()
    probes["database_rls"] = lambda: None
    report = StagingValidationHarness().run(probes)
    assert "database_rls" in report.unobservable


def test_chaos_failure_can_be_evidence_of_expected_safe_behavior():
    probes = _all_ok()
    scenario = ChaosScenario(
        "redis_loss",
        "critical distributed work refuses local fallback",
        lambda: ProbeResult("redis_loss", ProbeStatus.PASS, "distributed execution refused safely"),
    )
    report = StagingValidationHarness().run(probes, chaos=[scenario])
    assert report.passed is True
    assert report.results[-1].detail == "distributed execution refused safely"


def test_chaos_exception_is_a_failed_validation():
    probes = _all_ok()
    scenario = ChaosScenario("lease_loss", "stale worker cannot complete", lambda: 1 / 0)
    report = StagingValidationHarness().run(probes, chaos=[scenario])
    assert "lease_loss" in report.failures


def test_fail_closed_probe():
    assert fail_closed_probe("db", lambda: True).status is ProbeStatus.PASS
    assert fail_closed_probe("db", lambda: False).status is ProbeStatus.FAIL
    assert fail_closed_probe("db", lambda: (_ for _ in ()).throw(RuntimeError("down"))).status is ProbeStatus.FAIL
