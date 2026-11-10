"""Director reports must not present missing issue detail as an all-clear."""

from services.director import Director
from services.director.models import Health, Observation, Sector


def test_health_payload_distinguishes_health_state_from_issue_records():
    director = Director(supabase_client=None)
    director.observations = {
        "degraded-worker": Observation(
            source="degraded-worker",
            sector=Sector.CONTENT,
            status=Health.DEGRADED,
            issues=[],
        ),
        "unknown-worker": Observation(
            source="unknown-worker",
            sector=Sector.CONTENT,
            status=Health.UNKNOWN,
            issues=[],
        ),
    }

    health = director.health()

    assert health["issue_count"] == 0
    assert health["non_nominal_observer_count"] == 2
    assert health["unknown_observer_count"] == 1
    assert health["status_counts"] == {"degraded": 1, "unknown": 1}
    assert health["sectors"]["content"]["reported_issue_records"] == 0
    assert health["sectors"]["content"]["non_nominal_observers"] == 2


def test_executive_summary_does_not_call_high_score_excellent_with_degraded_sector():
    director = Director(supabase_client=None)
    healthy_sectors = [
        Sector.BRANDS,
        Sector.HEALTH,
        Sector.SECURITY,
        Sector.FINANCE,
        Sector.GROWTH,
        Sector.LEARNING,
        Sector.CLIENTS,
        Sector.INFRA,
    ]
    director.observations = {
        f"healthy-{sector.value}": Observation(
            source=f"healthy-{sector.value}",
            sector=sector,
            status=Health.NOMINAL,
        )
        for sector in healthy_sectors
    }
    director.observations["degraded-content"] = Observation(
        source="degraded-content",
        sector=Sector.CONTENT,
        status=Health.DEGRADED,
        issues=[],
    )

    report = director.generate_executive_summary()

    assert "overall score is high, but sector-level degradation" in report
    assert "0 issue records reported" in report
    assert "evidence gap, not proof that there are zero issues" in report
    assert "content**: DEGRADED" in report
    assert "Investigate degraded sector observations" in report
