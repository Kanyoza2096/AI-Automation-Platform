from services.director.observation_bridge import project_meta_health_evidence


def _health():
    return {
        "available": True,
        "status": "degraded",
        "health_score": 81,
        "risk_score": 22,
        "engine_observations": [
            {
                "service_id": "guardian",
                "status": "healthy",
                "observed_at": "2026-11-17T03:00:00+00:00",
                "age_seconds": 12,
                "source_version": "guardian-v2",
            },
            {
                "service_id": "scheduler",
                "status": "degraded",
                "observed_at": "2026-11-17T03:00:00+00:00",
                "age_seconds": 15,
                "source_version": "scheduler-v1",
            },
        ],
        "engine_observation_contract": {
            "contract": "meta_engine.observations/v1",
            "status": "complete",
            "source_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "freshness_seconds": 3600,
            "generated_at": "2026-11-17T03:00:12+00:00",
            "rejected": [],
        },
    }


def test_projection_preserves_supervisor_status_and_limits_authority():
    result = project_meta_health_evidence(_health())

    assert result["status"] == "complete"
    assert result["reported_system_status"] == "degraded"
    assert [item["service_id"] for item in result["observations"]] == ["guardian", "scheduler"]
    assert result["observations"][1]["status"] == "degraded"
    assert result["observations"][0]["independently_verified"] is False
    assert result["policy"]["authority"] == "supervisor_reported"
    assert result["policy"]["grants_execution_authority"] is False


def test_projection_does_not_invent_observations_when_contract_is_missing():
    health = {"available": True, "status": "healthy", "engine_states": {"guardian": "healthy"}}

    result = project_meta_health_evidence(health)

    assert result["status"] == "unavailable"
    assert result["observations"] == []
    assert result["policy"]["unknown_is_not_healthy"] is True


def test_projection_flags_contract_count_mismatch():
    health = _health()
    health["engine_observation_contract"]["accepted_count"] = 9

    result = project_meta_health_evidence(health)

    assert result["status"] == "inconsistent"
    assert result["integrity_status"] == "accepted_count_mismatch"


def test_projection_filters_malformed_observation_rows():
    health = _health()
    health["engine_observations"].append({"service_id": "missing-timestamp", "status": "healthy"})
    health["engine_observation_contract"]["accepted_count"] = 3

    result = project_meta_health_evidence(health)

    assert len(result["observations"]) == 2
    assert result["integrity_status"] == "consistent"
