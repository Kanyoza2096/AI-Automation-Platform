import pytest

from services.phase9_operational_intelligence import (
    OperationalPosture,
    OperationalRisk,
    OperationalSignal,
    compare_snapshots,
    synthesize,
)


def signal(workspace_id="ws_1", severity=OperationalRisk.LOW, **kw):
    data = dict(
        workspace_id=workspace_id,
        signal_type="dependency",
        source_ref="payments",
        severity=severity,
        summary="provider condition",
        evidence_ref="ev_1",
    )
    data.update(kw)
    return OperationalSignal(**data)


def test_empty_snapshot_is_normal():
    s = synthesize("ws_1")
    assert s.posture == OperationalPosture.NORMAL
    assert s.risk == OperationalRisk.NONE
    assert not s.requires_attention


def test_degraded_dependency_produces_degraded_posture():
    s = synthesize("ws_1", degraded_dependency_count=1)
    assert s.posture == OperationalPosture.DEGRADED
    assert s.requires_attention


def test_incident_out_ranks_generic_degradation():
    s = synthesize(
        "ws_1",
        incident_count=1,
        degraded_dependency_count=3,
    )
    assert s.posture == OperationalPosture.INCIDENT
    assert s.risk == OperationalRisk.MEDIUM


def test_reconciliation_out_ranks_incident():
    s = synthesize(
        "ws_1",
        incident_count=1,
        reconciliation_required_count=1,
    )
    assert s.posture == OperationalPosture.RECONCILIATION
    assert s.risk == OperationalRisk.HIGH


def test_security_hold_out_ranks_reconciliation():
    s = synthesize(
        "ws_1",
        reconciliation_required_count=2,
        security_hold_count=1,
    )
    assert s.posture == OperationalPosture.SECURITY_HOLD
    assert s.risk == OperationalRisk.CRITICAL


def test_quarantine_has_highest_precedence():
    s = synthesize(
        "ws_1",
        security_hold_count=1,
        quarantine_count=1,
    )
    assert s.posture == OperationalPosture.QUARANTINED
    assert s.risk == OperationalRisk.CRITICAL


def test_recovery_posture_is_distinct():
    s = synthesize("ws_1", recovery_required_count=1)
    assert s.posture == OperationalPosture.RECOVERY


def test_unknown_is_not_normal():
    s = synthesize("ws_1", unknown_count=1)
    assert s.posture == OperationalPosture.UNKNOWN
    assert s.requires_attention


def test_critical_signal_is_visible():
    s = synthesize(
        "ws_1",
        signals=(signal(severity=OperationalRisk.CRITICAL),),
    )
    assert s.critical_signal_count == 1
    assert s.posture == OperationalPosture.ATTENTION_REQUIRED
    assert s.risk == OperationalRisk.CRITICAL


def test_evidence_is_preserved():
    s = synthesize(
        "ws_1",
        signals=(
            signal(evidence_ref="ev_a"),
            signal(evidence_ref="ev_b"),
        ),
    )
    assert s.evidence_refs == ("ev_a", "ev_b")


def test_cross_workspace_signals_are_rejected():
    with pytest.raises(ValueError):
        synthesize(
            "ws_1",
            signals=(signal(workspace_id="ws_2"),),
        )


def test_negative_counts_are_rejected():
    with pytest.raises(ValueError):
        synthesize("ws_1", incident_count=-1)


def test_snapshot_counts_are_validated():
    with pytest.raises(ValueError):
        synthesize(
            "ws_1",
            signals=(signal(severity=OperationalRisk.LOW),),
        ).__class__(
            workspace_id="ws_1",
            posture=OperationalPosture.NORMAL,
            risk=OperationalRisk.NONE,
            signal_count=1,
            critical_signal_count=2,
            incident_count=0,
            degraded_dependency_count=0,
            reconciliation_required_count=0,
            recovery_required_count=0,
            security_hold_count=0,
            quarantine_count=0,
            unknown_count=0,
            dominant_causes=(),
            evidence_refs=(),
        )


def test_snapshot_comparison_is_descriptive():
    old = synthesize("ws_1", degraded_dependency_count=1)
    new = synthesize("ws_1", reconciliation_required_count=1)
    changes = compare_snapshots(old, new)
    assert "posture:DEGRADED->RECONCILIATION" in changes
    assert "reconciliation_required:0->1" in changes


def test_snapshot_comparison_preserves_workspace_boundary():
    with pytest.raises(ValueError):
        compare_snapshots(
            synthesize("ws_1"),
            synthesize("ws_2"),
        )


def test_snapshot_is_authority_free():
    s = synthesize("ws_1")
    with pytest.raises(ValueError):
        s.__class__(
            workspace_id="ws_1",
            posture=s.posture,
            risk=s.risk,
            signal_count=s.signal_count,
            critical_signal_count=s.critical_signal_count,
            incident_count=s.incident_count,
            degraded_dependency_count=s.degraded_dependency_count,
            reconciliation_required_count=s.reconciliation_required_count,
            recovery_required_count=s.recovery_required_count,
            security_hold_count=s.security_hold_count,
            quarantine_count=s.quarantine_count,
            unknown_count=s.unknown_count,
            dominant_causes=s.dominant_causes,
            evidence_refs=s.evidence_refs,
            authority="executor",
        )


def test_signal_is_authority_free():
    with pytest.raises(ValueError):
        signal(authority="executor")


def test_serialization_is_evidence_friendly():
    s = synthesize(
        "ws_1",
        signals=(signal(),),
        incident_count=1,
    )
    data = s.to_dict()
    assert data["posture"] == "INCIDENT"
    assert data["authority"] == "none"
    assert "evidence_refs" in data
