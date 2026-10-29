import pytest
from models.business_reliability import (
    BusinessReliabilityObjective,
    ReliabilitySignal,
    ReliabilitySignalKind,
    ReliabilityStatus,
    assess_business_reliability,
)
from services.business_reliability import BusinessReliabilityProjector


def objective(**kw):
    data = dict(workspace_id="ws_1", resource_ref="business:1", objective_id="obj_1")
    data.update(kw)
    return BusinessReliabilityObjective(**data)


def signal(kind, healthy, sid, value="ok"):
    return ReliabilitySignal(
        workspace_id="ws_1", resource_ref="business:1", kind=kind,
        value=value, healthy=healthy, signal_id=sid,
    )


def test_healthy_assessment_is_deterministic_and_authority_free():
    a = assess_business_reliability(objective(), (
        signal(ReliabilitySignalKind.SLO, True, "slo"),
        signal(ReliabilitySignalKind.DEPENDENCY, True, "dep"),
    ))
    b = assess_business_reliability(objective(), tuple(reversed((
        signal(ReliabilitySignalKind.DEPENDENCY, True, "dep"),
        signal(ReliabilitySignalKind.SLO, True, "slo"),
    ))))
    assert a.status == ReliabilityStatus.HEALTHY
    assert a.fingerprint == b.fingerprint
    assert a.authority == "none"
    assert a.safe_for_execution


def test_unknown_is_not_reported_as_healthy():
    a = assess_business_reliability(objective(), (
        signal(ReliabilitySignalKind.SLO, None, "slo", "unknown"),
    ))
    assert a.status == ReliabilityStatus.UNKNOWN
    assert not a.safe_for_execution


def test_reconciliation_debt_can_block_business_reliability():
    a = assess_business_reliability(objective(max_reconciliation_debt=0), (), reconciliation_debt=1)
    assert a.status == ReliabilityStatus.BLOCKED
    assert not a.safe_for_execution


def test_workspace_isolation_is_enforced():
    foreign = ReliabilitySignal(
        workspace_id="ws_2", resource_ref="business:1",
        kind=ReliabilitySignalKind.SLO, value="healthy", healthy=True, signal_id="slo",
    )
    with pytest.raises(ValueError):
        assess_business_reliability(objective(), (foreign,))


def test_projector_converges_existing_signals_without_execution():
    projector = BusinessReliabilityProjector()
    result = projector.assess(
        objective(),
        slo={"status": "healthy"},
        dependency_health=(),
        drift=(),
    )
    assert result.status == ReliabilityStatus.HEALTHY
    assert result.authority == "none"
    assert result.to_dict()["authority"] == "none"
    assert not hasattr(projector, "execute")
