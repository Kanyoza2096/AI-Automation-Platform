from datetime import datetime, timedelta, timezone

from services.director import Director
from services.director.models import Health, Observation, Sector


class RecordingAnalyzer:
    def __init__(self):
        self.calls = []

    def analyze(self, observations):
        self.calls.append(dict(observations))
        return None


def observation(source, sector, status, age_seconds=0):
    return Observation(
        source=source,
        sector=sector,
        status=status,
        issues=[{"type": "test_issue"}],
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


def test_stale_critical_observation_cannot_reach_diagnosis():
    director = Director(supabase_client=None)
    analyzer = RecordingAnalyzer()
    director.root_cause_analyzer = analyzer
    obs = observation("old-worker", Sector.HEALTH, Health.CRITICAL, age_seconds=900)

    director._run_diagnosis({"old-worker": obs})

    assert analyzer.calls == []
    assert director.tasks == {}
    assert director.last_diagnosis_evidence["rejected"][0]["reason"] == "observation_not_fresh"


def test_unknown_status_cannot_trigger_diagnosis():
    director = Director(supabase_client=None)
    analyzer = RecordingAnalyzer()
    director.root_cause_analyzer = analyzer
    obs = observation("unknown-worker", Sector.HEALTH, Health.UNKNOWN)

    director._run_diagnosis({"unknown-worker": obs})

    assert analyzer.calls == []
    assert director.tasks == {}
    assert director.last_diagnosis_evidence["rejected"][0]["reason"] == "source_status_unknown"


def test_conflicting_sector_is_excluded_from_automated_diagnosis():
    director = Director(supabase_client=None)
    analyzer = RecordingAnalyzer()
    director.root_cause_analyzer = analyzer
    observations = {
        "worker-a": observation("worker-a", Sector.HEALTH, Health.CRITICAL),
        "worker-b": observation("worker-b", Sector.HEALTH, Health.NOMINAL),
        "content-monitor": observation("content-monitor", Sector.CONTENT, Health.NOMINAL),
    }

    director._run_diagnosis(observations)

    assert len(analyzer.calls) == 1
    assert set(analyzer.calls[0]) == {"content-monitor"}
    assert director.last_diagnosis_evidence["conflicting_sectors"] == [Sector.HEALTH.value]
    assert {item["reason"] for item in director.last_diagnosis_evidence["rejected"]} == {"sector_status_conflict"}


def test_fresh_consistent_critical_evidence_reaches_diagnosis():
    director = Director(supabase_client=None)
    analyzer = RecordingAnalyzer()
    director.root_cause_analyzer = analyzer
    obs = observation("live-worker", Sector.HEALTH, Health.CRITICAL)

    director._run_diagnosis({"live-worker": obs})

    assert len(analyzer.calls) == 1
    assert list(analyzer.calls[0]) == ["live-worker"]
    assert director.last_diagnosis_evidence["accepted_observers"] == ["live-worker"]


def test_diagnostics_api_exposes_automated_diagnosis_evidence_gate(monkeypatch):
    from fastapi.testclient import TestClient
    from services.director import create_app

    monkeypatch.setenv("MASTER_API_TOKEN", "diagnosis-test-token")
    director = Director(supabase_client=None)
    director.last_diagnosis_evidence = {
        "max_age_seconds": 300,
        "input_count": 2,
        "accepted_observers": ["healthy-monitor"],
        "rejected": [{"observer": "old-monitor", "reason": "observation_not_fresh"}],
        "conflicting_sectors": ["health"],
    }
    client = TestClient(create_app(director))

    response = client.get(
        "/diagnostics",
        headers={"Authorization": "Bearer diagnosis-test-token"},
    )

    assert response.status_code == 200
    assert response.json()["evidence_gate"]["accepted_observers"] == ["healthy-monitor"]
    assert response.json()["evidence_gate"]["conflicting_sectors"] == ["health"]
