from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.meta_engine.core import MetaEngineV7


def _health(checked_at, status="healthy"):
    return SimpleNamespace(
        checked_at=checked_at,
        status=SimpleNamespace(value=status),
        version="test-health-v1",
    )


def test_observation_contract_excludes_stale_and_future_evidence():
    now = datetime(2026, 11, 16, 21, 10, tzinfo=timezone.utc)
    snapshot = SimpleNamespace(engines={
        "fresh": _health(now - timedelta(minutes=5)),
        "stale": _health(now - timedelta(hours=2)),
        "future": _health(now + timedelta(minutes=1)),
    })

    report = MetaEngineV7._engine_observation_contract_for_snapshot(snapshot, now=now)

    assert report["freshness_seconds"] == MetaEngineV7.STALE_AFTER_SECONDS
    assert report["accepted_count"] == 1
    assert report["observations"][0]["service_id"] == "fresh"
    assert {row["reason"] for row in report["rejected"]} == {
        "observation_stale",
        "observation_timestamp_in_future",
    }


def test_observation_contract_accepts_evidence_at_freshness_boundary():
    now = datetime(2026, 11, 16, 21, 10, tzinfo=timezone.utc)
    snapshot = SimpleNamespace(engines={
        "boundary": _health(now - timedelta(seconds=MetaEngineV7.STALE_AFTER_SECONDS)),
    })

    report = MetaEngineV7._engine_observation_contract_for_snapshot(snapshot, now=now)

    assert report["status"] == "complete"
    assert report["accepted_count"] == 1
    assert report["observations"][0]["age_seconds"] == MetaEngineV7.STALE_AFTER_SECONDS
