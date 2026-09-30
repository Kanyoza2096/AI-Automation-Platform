import pytest
from organism.operational_readiness import ReadinessStatus, assess_readiness


def test_ready_requires_observed_dependencies_and_valid_integrity():
    report = assess_readiness("w1", integrity_valid=True, dependencies={"db": True, "redis": True})
    assert report.status is ReadinessStatus.READY
    assert report.promotable is True


def test_unknown_external_state_never_becomes_ready():
    report = assess_readiness("w1", integrity_valid=True, dependencies={"db": None, "redis": True})
    assert report.status is ReadinessStatus.UNOBSERVABLE
    assert report.promotable is False
    assert "dependency unobserved: db" in report.warnings


def test_failed_dependency_blocks_promotion():
    report = assess_readiness("w1", integrity_valid=True, dependencies={"db": False})
    assert report.status is ReadinessStatus.BLOCKED
    assert "dependency unavailable: db" in report.blockers


def test_security_posture_threshold_blocks_promotion():
    report = assess_readiness("w1", integrity_valid=True, dependencies={"db": True}, security_posture=0.69)
    assert report.status is ReadinessStatus.BLOCKED


def test_invalid_security_posture_is_rejected():
    report = assess_readiness("w1", integrity_valid=True, dependencies={"db": True}, security_posture=1.5)
    assert report.status is ReadinessStatus.BLOCKED
    assert "security_posture must be between 0 and 1" in report.blockers


def test_cross_stage_workspace_is_not_inferred_or_granted():
    report = assess_readiness("", integrity_valid=True, dependencies={})
    assert report.status is ReadinessStatus.BLOCKED
    assert "workspace_id is empty" in report.blockers
