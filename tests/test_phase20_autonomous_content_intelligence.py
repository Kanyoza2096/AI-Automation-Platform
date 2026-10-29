"""P20: autonomous-content diagnostics use measured quality evidence only."""
from datetime import datetime, timezone

from core.autonomous_content import AutonomousContentEngine, ContentQuality
from services.director import Director
from services.director.models import Health, Observation, Sector


def _quality(score):
    return ContentQuality(
        overall=score, relevance=score, clarity=score,
        engagement_potential=score, brand_alignment=score,
        originality=score, readability=score, sentiment_alignment=score,
        suggestions=[], warnings=[],
    )


def test_quality_metrics_are_observational_and_empty_scores_do_not_signal():
    engine = AutonomousContentEngine()
    metrics = engine.check_health()["metrics"]
    assert metrics["quality_sample_count"] == 0
    assert metrics["average_quality_score"] is None
    assert metrics["low_quality_brands"] == 0
    assert metrics["director_signals"] == []
    assert metrics["director_signal_contract"] == "metrics.director_signals/v1"


def test_low_quality_signal_requires_three_real_scores_and_running_engine():
    engine = AutonomousContentEngine()
    engine._quality_scores["brand-1"] = [_quality(20), _quality(30), _quality(40)]
    # Old measurements alone do not create actionable work when stopped.
    assert engine.check_health()["metrics"]["director_signals"] == []

    engine._running = True
    result = engine.check_health()
    metrics = result["metrics"]
    assert result["status"] == "healthy"
    assert metrics["quality_sample_count"] == 3
    assert metrics["low_quality_sample_count"] == 3
    assert metrics["average_quality_score"] == 30
    assert metrics["low_quality_brands"] == 1
    signal, = metrics["director_signals"]
    assert signal["capability"] == "review_content_quality"
    assert signal["params"] == {"brand_id": "brand-1"}
    assert "3 recent measured quality scores" in signal["rationale"]


def test_insufficient_malformed_or_good_quality_scores_do_not_signal():
    engine = AutonomousContentEngine()
    engine._running = True
    engine._quality_scores["one"] = [_quality(10), _quality(20)]
    engine._quality_scores["good"] = [_quality(80), _quality(85), _quality(90)]
    engine._quality_scores["malformed"] = [object(), _quality(float("nan")), _quality(120)]
    metrics = engine.check_health()["metrics"]
    assert metrics["director_signals"] == []
    assert metrics["quality_sample_count"] == 5
    assert metrics["low_quality_brands"] == 0


def test_quality_advisory_passes_director_content_sector_validation():
    engine = AutonomousContentEngine()
    engine._running = True
    engine._quality_scores["brand-2"] = [_quality(35), _quality(40), _quality(45)]
    health = engine.check_health()
    director = Director(supabase_client=None)
    director.observations["autonomous_content"] = Observation(
        source="autonomous_content", sector=Sector.CONTENT, status=Health.NOMINAL,
        metrics=health["metrics"], timestamp=datetime.now(timezone.utc),
    )
    report = director._engine_decision_signals()
    assert len(report["accepted"]) == 1
    assert report["accepted"][0]["capability"] == "review_content_quality"
    assert report["accepted"][0]["params"] == {"brand_id": "brand-2"}
    assert report["rejected"] == []
