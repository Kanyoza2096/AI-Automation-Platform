from datetime import datetime, timezone
from types import SimpleNamespace

from services.meta_engine.core import MetaEngineV7


def test_meta_engine_observation_contract_reports_accepted_and_rejected_rows():
    snapshot = SimpleNamespace(engines={
        "guardian": SimpleNamespace(
            checked_at=datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc),
            status=SimpleNamespace(value="healthy"),
            version="guardian-health-v1",
        ),
        "job_service": SimpleNamespace(
            checked_at=None,
            status=SimpleNamespace(value="unknown"),
            version="job-health-v1",
        ),
    })

    report = MetaEngineV7._engine_observation_contract_for_snapshot(snapshot)

    assert report["contract"] == "meta_engine.observations/v1"
    assert report["status"] == "partial"
    assert report["source_count"] == 2
    assert report["accepted_count"] == 1
    assert report["rejected_count"] == 1
    assert report["observations"][0]["service_id"] == "guardian"
    assert report["rejected"] == [{
        "service_id": "job_service",
        "reason": "timestamp_missing_or_timezone_naive",
    }]


def test_meta_engine_observation_contract_rejects_timezone_naive_timestamps():
    snapshot = SimpleNamespace(engines={
        "guardian": SimpleNamespace(
            checked_at=datetime(2026, 11, 16, 20, 0),
            status="healthy",
            version="guardian-health-v1",
        ),
    })

    report = MetaEngineV7._engine_observation_contract_for_snapshot(snapshot)

    assert report["status"] == "partial"
    assert report["accepted_count"] == 0
    assert report["rejected"][0]["reason"] == "timestamp_missing_or_timezone_naive"


def test_meta_engine_observation_contract_fails_closed_for_invalid_source():
    report = MetaEngineV7._engine_observation_contract_for_snapshot(
        SimpleNamespace(engines=None)
    )

    assert report["status"] == "invalid_source"
    assert report["accepted_count"] == 0
    assert report["rejected"][0]["reason"] == "engine_health_map_invalid"
