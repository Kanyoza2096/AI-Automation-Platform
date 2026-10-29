from organism.staging_evidence import FailureClass, StagingEvidenceCollector, verify_evidence
from organism.staging_validation import ProbeResult, ProbeStatus, StagingValidationHarness


def probes():
    return {name: (lambda: True) for name in StagingValidationHarness.REQUIRED_PROBES}


def test_evidence_has_stable_digest_and_verifies():
    report = StagingValidationHarness().run(probes())
    promotion = StagingEvidenceCollector().collect(report, correlation_id="corr-1")
    assert promotion.promotable is True
    assert promotion.correlation_id == "corr-1"
    assert all(verify_evidence(item) for item in promotion.evidence)


def test_failed_and_unobservable_probes_block_promotion():
    p = probes()
    p["redis"] = lambda: ProbeResult("redis", ProbeStatus.FAIL, "connection refused")
    p.pop("provider_adapter")
    report = StagingValidationHarness().run(p)
    promotion = StagingEvidenceCollector().collect(report, failure_classes={"redis": FailureClass.DEPENDENCY})
    assert promotion.promotable is False
    assert "redis" in promotion.blockers
    assert "provider_adapter" in promotion.blockers
    redis = next(x for x in promotion.evidence if x.probe == "redis")
    assert redis.failure_class is FailureClass.DEPENDENCY


def test_tampered_evidence_is_detected():
    report = StagingValidationHarness().run(probes())
    promotion = StagingEvidenceCollector().collect(report)
    original = promotion.evidence[0]
    forged = type(original)(**{**original.__dict__, "detail": "forged"})
    assert verify_evidence(original) is True
    assert verify_evidence(forged) is False


def test_json_report_is_machine_readable():
    import json
    report = StagingValidationHarness().run(probes())
    promotion = StagingEvidenceCollector().collect(report)
    parsed = json.loads(promotion.to_json())
    assert parsed["promotable"] is True
    assert len(parsed["evidence"]) == len(report.results)
