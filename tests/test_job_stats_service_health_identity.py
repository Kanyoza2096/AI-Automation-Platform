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


def test_job_stats_health_is_unknown_for_malformed_metrics_not_healthy():
    service = JobStatsService(supabase_client=object())
    service.get_stats = lambda brand_id=None: {
        "status_counts": {"FAILED": "not-a-number", "STALE": 0},
        "active_jobs": 0,
        "oldest_active_minutes": 0,
    }

    result = service.health_check()

    assert result["status"] == "unknown"
    assert result["stats"] is None
    assert "invalid_stats_payload" in result["error"]


def test_failure_intelligence_classifies_evidence_gate_without_retrying():
    diagnosis = JobStatsService._classify_failed_job({
        "job_id": "job-evidence",
        "last_error_code": "WORKFLOW_FAILED",
        "attempt_count": 1,
        "max_attempts": 3,
        "result": {
            "failed_steps": [{
                "step": "generate_content",
                "error_code": "AI_VALIDATION_ERROR",
                "error": "Factual evidence gate rejected content: requires a verified KB source with a stable source ID",
            }]
        },
    })

    assert diagnosis["category"] == "evidence_grounding"
    assert diagnosis["job_id"] == "job-evidence"
    assert "verified knowledge-base source" in diagnosis["recommended_action"]


def test_failure_intelligence_classifies_voice_and_caption_failures():
    voice = JobStatsService._classify_failed_job({
        "result": {"failed_steps": [{"step": "validate_content", "error": "caption_quality: first_person_violation: forbidden first-person pronoun pattern"}]}
    })
    caption = JobStatsService._classify_failed_job({
        "result": {"failed_steps": [{"step": "validate_content", "error": "caption too short: 79 words; minimum 80"}]}
    })

    assert voice["category"] == "voice_compliance"
    assert caption["category"] == "caption_quality"


def test_failure_intelligence_preserves_unknown_failures_for_investigation():
    diagnosis = JobStatsService._classify_failed_job({
        "job_id": "job-unknown",
        "last_error_code": "WORKFLOW_FAILED",
        "result": {"failed_steps": [{"step": "publish", "error": "unrecognized provider response"}]},
    })

    assert diagnosis["category"] == "unclassified"
    assert diagnosis["job_id"] == "job-unknown"
    assert "Inspect" in diagnosis["recommended_action"]



def test_job_stats_query_error_state_is_isolated_between_threads():
    from threading import Thread

    service = JobStatsService(supabase_client=object())
    service._last_error = "main-thread failure"
    observed = []

    def worker():
        observed.append(service._last_error)
        service._last_error = "worker-thread failure"

    thread = Thread(target=worker)
    thread.start()
    thread.join()

    assert observed == [None]
    assert service._last_error == "main-thread failure"
