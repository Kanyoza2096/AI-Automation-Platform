from services.job_stats_service import JobStatsService


def test_job_stats_health_provider_uses_canonical_platform_state_identity():
    service = JobStatsService()

    assert service.service_name == "job_stats_service"
