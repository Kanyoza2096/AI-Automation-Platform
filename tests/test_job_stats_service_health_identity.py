from services.job_stats_service import JobStatsService


def test_job_stats_health_provider_uses_canonical_platform_state_identity():
    service = JobStatsService()

    assert service.service_name == "job_stats_service"


def test_job_stats_health_is_unknown_when_both_stats_queries_fail():
    service = JobStatsService(supabase_client=object())
    service._compute_stats_via_rpc = lambda brand_id: (_ for _ in ()).throw(
        RuntimeError("RPC unavailable")
    )
    service._compute_stats_client_side = lambda brand_id: (_ for _ in ()).throw(
        RuntimeError("database unavailable")
    )

    result = service.health_check()

    assert result["status"] == "unknown"
    assert "database unavailable" in result["error"]
    assert result["stats"] is None


def test_job_stats_health_recovers_after_a_successful_query():
    service = JobStatsService(supabase_client=object())
    service._compute_stats_via_rpc = lambda brand_id: (_ for _ in ()).throw(
        RuntimeError("RPC unavailable")
    )
    service._compute_stats_client_side = lambda brand_id: (_ for _ in ()).throw(
        RuntimeError("database unavailable")
    )

    failed = service.health_check()
    assert failed["status"] == "unknown"

    service._compute_stats_via_rpc = lambda brand_id: {
        "status_counts": {"FAILED": 12, "STALE": 0},
        "active_jobs": 0,
        "oldest_active_minutes": 0,
    }
    recovered = service.health_check()

    assert recovered["status"] == "degraded"
    assert recovered["stats"]["status_counts"]["FAILED"] == 12
