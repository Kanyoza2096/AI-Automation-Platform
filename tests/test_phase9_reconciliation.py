import pytest

from services.phase9_reconciliation import (
    AuthoritativeObservation,
    DriftSeverity,
    ExpectedState,
    ReconciliationStatus,
    can_resume_after_reconciliation,
    reconcile,
)


def expected(**kw):
    data = dict(
        workspace_id="ws_1",
        resource_ref="asset:42",
        state={"status": "active", "owner": "ops"},
    )
    data.update(kw)
    return ExpectedState(**data)


def observed(**kw):
    data = dict(
        workspace_id="ws_1",
        resource_ref="asset:42",
        state={"status": "active", "owner": "ops"},
        observed_at="2026-10-05T08:00:00Z",
        evidence_ref="ev_1",
    )
    data.update(kw)
    return AuthoritativeObservation(**data)


def test_matching_state_is_matched():
    finding = reconcile(expected(), observed())
    assert finding.status == ReconciliationStatus.MATCHED
    assert finding.severity == DriftSeverity.NONE
    assert not finding.blocks_unsafe_execution


def test_single_field_drift_is_low():
    finding = reconcile(
        expected(),
        observed(state={"status": "disabled", "owner": "ops"}),
    )
    assert finding.status == ReconciliationStatus.DRIFT_DETECTED
    assert finding.severity == DriftSeverity.LOW
    assert finding.differing_fields == ("status",)
    assert finding.blocks_unsafe_execution


def test_multiple_field_drift_is_escalated():
    finding = reconcile(
        expected(),
        observed(state={"status": "disabled", "owner": "other", "region": "eu"}),
    )
    assert finding.status == ReconciliationStatus.DRIFT_DETECTED
    assert finding.severity == DriftSeverity.MEDIUM


def test_missing_observation_is_not_match():
    finding = reconcile(expected(), None)
    assert finding.status == ReconciliationStatus.OBSERVATION_UNAVAILABLE
    assert finding.blocks_unsafe_execution


def test_unknown_is_conservative():
    # An observation with unknown-valued fields is a mismatch, never a match.
    finding = reconcile(
        expected(),
        observed(state={"status": None, "owner": "ops"}),
    )
    assert finding.status == ReconciliationStatus.DRIFT_DETECTED
    assert finding.blocks_unsafe_execution


def test_workspace_isolation():
    with pytest.raises(ValueError):
        reconcile(
            expected(),
            observed(workspace_id="ws_2"),
        )


def test_resource_identity_is_required():
    with pytest.raises(ValueError):
        reconcile(
            expected(),
            observed(resource_ref="asset:99"),
        )


def test_evidence_is_preserved():
    finding = reconcile(
        expected(source_evidence_ref="ev_expected"),
        observed(evidence_ref="ev_observed"),
    )
    assert finding.evidence_refs == ("ev_expected", "ev_observed")


def test_authority_free_expected_state():
    with pytest.raises(ValueError):
        expected(authority="executor")


def test_authority_free_observation():
    with pytest.raises(ValueError):
        observed(authority="executor")


def test_authority_free_finding():
    finding = reconcile(expected(), observed())
    with pytest.raises(ValueError):
        finding.__class__(
            workspace_id="ws_1",
            resource_ref="asset:42",
            status=ReconciliationStatus.MATCHED,
            severity=DriftSeverity.NONE,
            differing_fields=(),
            expected_id=finding.expected_id,
            observation_id=finding.observation_id,
            evidence_refs=finding.evidence_refs,
            authority="executor",
        )


def test_resume_requires_explicit_resolution():
    finding = reconcile(
        expected(),
        observed(state={"status": "disabled", "owner": "ops"}),
    )
    assert not can_resume_after_reconciliation(finding, resolved=False)
    assert not can_resume_after_reconciliation(finding, resolved=True)


def test_matched_state_can_clear_reconciliation_boundary():
    finding = reconcile(expected(), observed())
    assert can_resume_after_reconciliation(finding, resolved=True)


def test_resolution_is_not_implicit_from_drift():
    finding = reconcile(
        expected(),
        observed(state={"status": "disabled", "owner": "ops"}),
    )
    assert finding.status != ReconciliationStatus.RESOLVED


def test_added_and_removed_keys_are_detected():
    finding = reconcile(
        expected(),
        observed(state={"status": "active", "region": "africa"}),
    )
    assert set(finding.differing_fields) == {"owner", "region"}


def test_serialization_is_evidence_friendly():
    finding = reconcile(expected(), observed())
    data = finding.to_dict()
    assert data["status"] == "MATCHED"
    assert data["authority"] == "none"
    assert "evidence_refs" in data


def test_observation_requires_evidence_reference():
    with pytest.raises(ValueError):
        observed(evidence_ref="")


def test_expected_state_requires_workspace_and_resource():
    with pytest.raises(ValueError):
        expected(workspace_id="")
    with pytest.raises(ValueError):
        expected(resource_ref="")
