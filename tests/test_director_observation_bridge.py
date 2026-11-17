from services.director.observation_bridge import (
    project_meta_health_evidence,
    reconcile_supervisor_evidence,
)


def _health():
    return {
        "available": True,
        "status": "degraded",
        "health_score": 81,
        "risk_score": 22,
        "engine_observations": [
            {"service_id": "guardian", "status": "healthy", "observed_at": "2026-11-17T03:00:00+00:00", "age_seconds": 12, "source_version": "guardian-v2"},
            {"service_id": "scheduler", "status": "degraded", "observed_at": "2026-11-17T03:00:00+00:00", "age_seconds": 15, "source_version": "scheduler-v1"},
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
    result = project_meta_health_evidence({"available": True, "status": "healthy", "engine_states": {"guardian": "healthy"}})
    assert result["status"] == "unavailable"
    assert result["observations"] == []
    assert result["policy"]["unknown_is_not_healthy"] is True


def test_projection_flags_contract_count_mismatch():
    health = _health()
    health["engine_observation_contract"]["accepted_count"] = 9
    result = project_meta_health_evidence(health)
    assert result["status"] == "inconsistent"
    assert result["integrity_status"] == "observation_count_mismatch"


def test_projection_rejects_malformed_observation_rows():
    health = _health()
    health["engine_observations"].append({"service_id": "missing-timestamp", "status": "healthy"})
    result = project_meta_health_evidence(health)
    assert len(result["observations"]) == 2
    assert result["integrity_status"] == "observation_count_mismatch"


def test_reconciliation_detects_exact_identity_disagreement():
    evidence = project_meta_health_evidence(_health())
    context = {
        "facts": [
            {"kind": "source_reported_status", "observer": "guardian", "source": "guardian", "status": "critical"},
            {"kind": "source_reported_status", "observer": "scheduler", "source": "scheduler", "status": "degraded"},
        ],
        "unknowns": [],
        "conflicts": [],
    }
    result = reconcile_supervisor_evidence(context, evidence)
    reconciliation = result["supervisor_evidence_reconciliation"]
    assert reconciliation["status"] == "disagreement"
    assert reconciliation["counts"]["disagreements"] == 1
    assert reconciliation["counts"]["agreements"] == 1
    assert any(item["kind"] == "supervisor_director_status_disagreement" for item in result["conflicts"])
    assert reconciliation["grants_execution_authority"] is False


def test_reconciliation_never_guesses_identity_and_marks_unmatched_unknown():
    evidence = project_meta_health_evidence(_health())
    context = {"facts": [{"observer": "guardian-service", "source": "guardian-service", "status": "healthy"}], "unknowns": [], "conflicts": []}
    result = reconcile_supervisor_evidence(context, evidence)
    reconciliation = result["supervisor_evidence_reconciliation"]
    assert reconciliation["counts"]["unmatched"] == 2
    assert reconciliation["status"] == "partial"
    assert any(item["kind"] == "supervisor_observation_not_matched" for item in result["unknowns"])


def test_reconciliation_does_not_claim_independence_for_agreement():
    evidence = project_meta_health_evidence(_health())
    context = {"facts": [{"observer": "guardian", "source": "guardian", "status": "nominal"}, {"observer": "scheduler", "source": "scheduler", "status": "degraded"}], "unknowns": [], "conflicts": []}
    result = reconcile_supervisor_evidence(context, evidence)
    reconciliation = result["supervisor_evidence_reconciliation"]
    assert reconciliation["status"] == "agreement"
    assert reconciliation["counts"]["agreements"] == 2
    assert reconciliation["independence_established"] is False
