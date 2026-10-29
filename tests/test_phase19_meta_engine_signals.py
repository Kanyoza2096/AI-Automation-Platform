"""P19: Meta Engine recommendations are grounded in explicit snapshot evidence."""
from datetime import datetime, timezone

from services.meta_engine.core import MetaEngineV7
from services.meta_engine.models import EngineHealth, EngineStatus, SystemSnapshot
from services.director import Director
from services.director.models import Health, Observation, Sector


def _meta(snapshot):
    engine = MetaEngineV7()
    engine._current_snapshot = snapshot
    return engine


def test_meta_engine_publishes_health_scan_advisory_for_explicit_stale_engine():
    snapshot = SystemSnapshot(
        overall_status=EngineStatus.NOMINAL,
        health_score=95,
        risk_score=5,
        engines={"content_engine": EngineHealth(name="content_engine", status=EngineStatus.STALE)},
    )
    result = _meta(snapshot).check_health()
    signals = result["metrics"]["director_signals"]
    assert result["status"] == "healthy"
    assert len(signals) == 1
    assert signals[0]["capability"] == "run_health_scan"
    assert signals[0]["params"] == {}
    assert "content_engine" in signals[0]["rationale"]
    assert result["metrics"]["director_signal_contract"] == "metrics.director_signals/v1"


def test_meta_engine_does_not_publish_advisory_for_degraded_or_unknown_snapshot():
    for state in (EngineStatus.DEGRADED, EngineStatus.UNKNOWN):
        snapshot = SystemSnapshot(
            overall_status=state,
            engines={"content_engine": EngineHealth(name="content_engine", status=EngineStatus.STALE)},
        )
        assert _meta(snapshot).check_health()["metrics"]["director_signals"] == []


def test_meta_engine_stale_advisory_passes_director_health_sector_validation():
    snapshot = SystemSnapshot(
        overall_status=EngineStatus.NOMINAL,
        engines={"content_engine": EngineHealth(name="content_engine", status=EngineStatus.STALE)},
    )
    health = _meta(snapshot).check_health()
    director = Director(supabase_client=None)
    director.observations["meta_engine"] = Observation(
        source="meta_engine", sector=Sector.HEALTH, status=Health.NOMINAL,
        metrics=health["metrics"], timestamp=datetime.now(timezone.utc),
    )
    report = director._engine_decision_signals()
    assert len(report["accepted"]) == 1
    assert report["accepted"][0]["capability"] == "run_health_scan"
    assert report["rejected"] == []
