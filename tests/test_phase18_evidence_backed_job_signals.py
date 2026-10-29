"""P18: JobService publishes only evidence-backed, non-urgent backlog advisories."""
from datetime import datetime, timezone

from services.director import Director
from services.director.models import Health, Observation, Sector
from services.job_service import JobService


def _health(status="healthy", failed=2, stale=1):
    class Stats:
        def health_check(self):
            return {
                "status": status,
                "stats": {"status_counts": {"FAILED": failed, "STALE": stale}},
            }
    return Stats()


def test_job_service_publishes_backlog_advisory_from_explicit_healthy_counts(monkeypatch):
    import services.job_stats_service as module
    monkeypatch.setattr(module, "get_job_stats_service", lambda: _health())

    result = JobService().health_check()
    signals = result["metrics"]["director_signals"]

    assert result["status"] == "healthy"
    assert len(signals) == 1
    assert signals[0]["capability"] == "optimize_infrastructure"
    assert signals[0]["params"] == {"component": "job_service"}
    assert "FAILED=2, STALE=1" in signals[0]["rationale"]
    assert result["metrics"]["director_signal_contract"] == "metrics.director_signals/v1"


def test_job_service_does_not_publish_from_degraded_or_unknown_health(monkeypatch):
    import services.job_stats_service as module
    monkeypatch.setattr(module, "get_job_stats_service", lambda: _health("degraded"))
    assert JobService().health_check()["metrics"]["director_signals"] == []

    monkeypatch.setattr(module, "get_job_stats_service", lambda: _health("unknown"))
    assert JobService().health_check()["metrics"]["director_signals"] == []


def test_job_service_does_not_infer_missing_or_malformed_counts(monkeypatch):
    import services.job_stats_service as module

    class MissingStats:
        def health_check(self):
            return {"status": "healthy", "stats": {}}

    class MalformedStats:
        def health_check(self):
            return {"status": "healthy", "stats": {"status_counts": {"FAILED": "many", "STALE": 1}}}

    monkeypatch.setattr(module, "get_job_stats_service", lambda: MissingStats())
    assert JobService().health_check()["metrics"]["director_signals"] == []
    monkeypatch.setattr(module, "get_job_stats_service", lambda: MalformedStats())
    assert JobService().health_check()["metrics"]["director_signals"] == []


def test_backlog_advisory_passes_director_sector_and_capability_validation(monkeypatch):
    import services.job_stats_service as module
    monkeypatch.setattr(module, "get_job_stats_service", lambda: _health())
    health = JobService().health_check()
    director = Director(supabase_client=None)
    director.observations["job_service"] = Observation(
        source="job_service",
        sector=Sector.INFRA,
        status=Health.NOMINAL,
        metrics=health["metrics"],
        timestamp=datetime.now(timezone.utc),
    )

    report = director._engine_decision_signals()
    assert len(report["accepted"]) == 1
    assert report["accepted"][0]["capability"] == "optimize_infrastructure"
    assert report["rejected"] == []
